# py: ==3.14.*
"""Facts-first daily briefing with drill-down identifiers and cost provenance."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, TypedDict

import psycopg
from psycopg.rows import dict_row


class postgresql(TypedDict):
    host: str
    port: int
    user: str
    password: str
    dbname: str
    sslmode: str


def _json(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    return value


def _rows(cur, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    cur.execute(sql, args)
    return [_json(dict(row)) for row in cur.fetchall()]


def _action(item: dict[str, Any]) -> tuple[str, str]:
    priority = float(item.get("priority") or 0)
    if item.get("l3_completed"):
        return "查看精研并决定归档", "已有完成且通过隐私审核的 L3 结果"
    if priority >= 80:
        return "优先人工复核", f"规则优先级 {priority:.0f} 分"
    if priority >= 60:
        return "继续跟踪", f"规则优先级 {priority:.0f} 分，尚未完成 L3"
    return "保留观察", f"规则优先级 {priority:.0f} 分"


def main(
    db: postgresql,
    platform: str = "all",
    hours: int = 24,
    limit: int = 12,
):
    platform = (platform or "all").strip()
    hours = max(1, min(int(hours or 24), 720))
    limit = max(1, min(int(limit or 12), 30))
    connect_args = {
        "host": db["host"],
        "port": int(db.get("port", 5432)),
        "user": db["user"],
        "password": db["password"],
        "dbname": db["dbname"],
        "sslmode": db.get("sslmode", "prefer"),
    }
    with psycopg.connect(**connect_args) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("set transaction read only")
        kpis = _rows(
            cur,
            """
            with latest_score as (
              select distinct on (video_id) video_id, score
              from video_score
              where score_type='priority'
              order by video_id, calculated_at desc, id desc
            )
            select
              count(*)::int as observed_videos,
              count(*) filter (
                where coalesce(s.score, v.monitoring_priority, 0) >= 60
              )::int as priority_candidates,
              count(*) filter (where exists(
                select 1 from analysis_run ar
                join research_task_cost c on c.id=ar.task_cost_id
                where ar.video_id=v.id
                  and ar.analysis_level='L3'
                  and ar.analysis_type='l3_structured_research'
                  and ar.status='completed'
                  and ar.schema_version='l3-research-v1.0.0'
                  and ar.output->>'privacy_reviewed'='true'
                  and c.status='completed'
              ))::int as l3_completed
            from source_video v
            left join latest_score s on s.video_id=v.id
            where (%s='all' or v.platform=%s)
              and v.last_seen_at >= now() - (%s || ' hours')::interval
              and v.availability_status='available'
            """,
            (platform, platform, hours),
        )[0]
        items = _rows(
            cur,
            """
            with latest_score as (
              select distinct on (video_id)
                video_id, score, rule_version, calculated_at
              from video_score
              where score_type='priority'
              order by video_id, calculated_at desc, id desc
            ), source_hits as (
              select video_id,
                     array_agg(distinct source_type order by source_type) as sources
              from discovery_event
              group by video_id
            )
            select
              v.id::text as video_id,
              v.platform,
              coalesce(v.title, v.description, '(无标题)') as title,
              a.nickname as account_name,
              v.published_at,
              v.last_seen_at,
              v.research_level,
              v.monitoring_status,
              coalesce(s.score, v.monitoring_priority, 0)::numeric as priority,
              s.rule_version,
              m.play_count,
              m.like_count,
              m.comment_count,
              m.share_count,
              m.author_follower_count,
              m.captured_at as metric_captured_at,
              m.metric_provenance,
              coalesce(h.sources, array[]::text[]) as sources,
              exists(
                select 1 from analysis_run ar
                join research_task_cost c on c.id=ar.task_cost_id
                where ar.video_id=v.id
                  and ar.analysis_level='L3'
                  and ar.analysis_type='l3_structured_research'
                  and ar.status='completed'
                  and ar.schema_version='l3-research-v1.0.0'
                  and ar.output->>'privacy_reviewed'='true'
                  and c.status='completed'
              ) as l3_completed
            from source_video v
            left join source_account a on a.id=v.account_id
            left join merged_video_metric m on m.video_id=v.id
            left join latest_score s on s.video_id=v.id
            left join source_hits h on h.video_id=v.id
            where (%s='all' or v.platform=%s)
              and v.last_seen_at >= now() - (%s || ' hours')::interval
              and v.availability_status='available'
            order by coalesce(s.score, v.monitoring_priority, 0) desc,
                     v.last_seen_at desc, v.id
            limit %s
            """,
            (platform, platform, hours, limit),
        )
        for item in items:
            item["suggested_action"], item["action_reason"] = _action(item)

        briefs = _rows(
            cur,
            """
            select
              count(*) filter (where status='active')::int as active,
              count(*) filter (where status='active' and next_due_at <= now())::int as due,
              min(next_due_at) filter (where status='active') as next_due_at
            from research_brief
            """,
        )[0]
        runs = _rows(
            cur,
            """
            select
              count(*)::int as total,
              count(*) filter (where status='success')::int as success,
              count(*) filter (where status='failed')::int as failed,
              count(*) filter (where status='deferred')::int as deferred
            from research_brief_run
            where started_at >= now() - (%s || ' hours')::interval
            """,
            (hours,),
        )[0]
        api_costs = _rows(
            cur,
            """
            select provider, coalesce(cost_currency, 'UNKNOWN') as currency,
                   count(*)::int as call_count,
                   coalesce(sum(case
                     when coalesce(metadata->>'cost_basis', '') in
                       ('estimated_unit_price', 'verified_unit_price')
                     then coalesce(estimated_cost, actual_cost, 0) else 0 end), 0)::numeric
                     as estimated_cost,
                   coalesce(sum(case
                     when metadata->>'cost_basis'='supplier_bill'
                     then coalesce(actual_cost, 0) else 0 end), 0)::numeric
                     as reconciled_cost,
                   count(*) filter (where metadata->>'billing_status'='unknown')::int
                     as unknown_cost_calls
            from external_api_call
            where started_at >= now() - (%s || ' hours')::interval
              and (%s='all' or platform=%s)
            group by provider, coalesce(cost_currency, 'UNKNOWN')
            order by provider, currency
            """,
            (hours, platform, platform),
        )
        task_costs = _rows(
            cur,
            """
            select cost_currency as currency, cost_basis,
                   count(*)::int as task_count,
                   coalesce(sum(api_cost), 0)::numeric as api_cost,
                   coalesce(sum(asr_cost), 0)::numeric as asr_cost,
                   coalesce(sum(llm_cost), 0)::numeric as llm_cost,
                   coalesce(sum(total_cost), 0)::numeric as known_total
            from research_task_cost c
            left join source_video v on v.id=c.video_id
            where c.created_at >= now() - (%s || ' hours')::interval
              and (%s='all' or v.platform=%s)
            group by cost_currency, cost_basis
            order by cost_currency, cost_basis
            """,
            (hours, platform, platform),
        )
        supplier_spend = _rows(
            cur,
            """
            select distinct on (provider, account_scope, bill_scope_key, cost_currency)
              provider, account_scope, bill_scope_key, scope_kind, scope_label,
              billing_date, cost_currency, billing_timezone, total_cost,
              total_requests, paid_requests, billing_finality, source_warning, fetched_at
            from supplier_daily_spend
            order by provider, account_scope, bill_scope_key, cost_currency,
                     billing_date desc, fetched_at desc
            """,
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform,
        "hours": hours,
        "kpis": kpis,
        "items": items,
        "briefs": briefs,
        "runs": runs,
        "costs": {
            "supplier_daily_spend": supplier_spend,
            "api_call_estimates": api_costs,
            "research_task_costs": task_costs,
            "actual_cost_source": "supplier_daily_spend",
            "estimate_source": "external_api_call_or_research_task_cost",
        },
        "interpretation": "规则排序的事实简报，不是模型自动生成的业务结论。",
        "raw_data_drilldown": "使用 video_id 打开视频库，可查看指标来源、历史快照、评论样本、转写和已审核 L3 结果。",
    }
