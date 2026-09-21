from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, TypedDict
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg
from psycopg.rows import dict_row


class postgresql(TypedDict):
    host: str
    port: int
    user: str
    password: str
    dbname: str
    sslmode: str


def _params(platform: str, hours: int) -> tuple[str, str, int]:
    platform = (platform or "all").strip()
    hours = max(1, min(int(hours or 24), 24 * 30))
    return platform, platform, hours


def _json(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json(v) for v in value]
    return value


def _fetch_all(conn, sql: str, args=()) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, args)
        return [_json(dict(row)) for row in cur.fetchall()]


def _fetch_one(conn, sql: str, args=()) -> dict[str, Any]:
    rows = _fetch_all(conn, sql, args)
    return rows[0] if rows else {}


def _daily_supplier_spend(conn, days: int = 7) -> dict[str, Any]:
    """Read supplier-account daily totals; never blend them with platform data."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("select to_regclass('public.supplier_daily_spend') as table_name")
        if cur.fetchone()["table_name"] is None:
            return {
                "status": "not_synced",
                "records": [],
                "today": [],
                "message": "供应商日费用尚未同步。",
            }
        cur.execute(
            """
            select provider, account_scope, bill_scope_key, scope_kind, scope_label,
                   billing_date, cost_currency, billing_timezone, total_cost,
                   balance_cost, free_credit_cost, payable_cost, paid_cost, unpaid_cost,
                   total_requests, paid_requests, billing_finality, source_warning, fetched_at
            from supplier_daily_spend
            where billing_date >= current_date - %s
            order by billing_date desc, provider, account_scope, bill_scope_key, cost_currency
            """,
            (max(days, 7),),
        )
        rows = [_json(dict(row)) for row in cur.fetchall()]

    now = datetime.now(timezone.utc)
    for row in rows:
        timezone_name = row.get("billing_timezone") or "UTC"
        try:
            supplier_today = now.astimezone(ZoneInfo(timezone_name)).date().isoformat()
        except ZoneInfoNotFoundError:
            supplier_today = None
        try:
            fetched = datetime.fromisoformat(str(row.get("fetched_at")).replace("Z", "+00:00"))
            if fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=timezone.utc)
            stale = now - fetched.astimezone(timezone.utc) > timedelta(hours=2)
        except (TypeError, ValueError):
            stale = True
        row["supplier_current_date"] = supplier_today
        row["period_status"] = (
            "current_accumulating" if supplier_today and row["billing_date"] == supplier_today
            else "prior_snapshot"
        )
        row["freshness_status"] = "stale" if stale else "fresh"
    today = [row for row in rows if row["period_status"] == "current_accumulating"]
    return {
        "status": "available" if rows else "not_synced",
        "records": rows,
        "today": today,
        "message": None if rows else "尚未同步到供应商日费用记录。",
    }


def main(db: postgresql, platform: str = "douyin", hours: int = 24):
    selected, selected2, hours = _params(platform, hours)

    connect_args = {
        "host": db["host"],
        "port": int(db.get("port", 5432)),
        "user": db["user"],
        "password": db["password"],
        "dbname": db["dbname"],
        "sslmode": db.get("sslmode", "prefer"),
    }

    with psycopg.connect(**connect_args) as conn:
        platforms = _fetch_all(
            conn,
            """
            select platform_key as key, display_name as name, enabled, provider_status, sort_order
            from platform_registry
            order by sort_order, platform_key
            """,
        )

        kpis = _fetch_one(
            conn,
            """
            with selected_videos as (
              select id
              from source_video
              where (%s='all' or platform=%s)
            ),
            hot as (
              select count(*)::int value
              from external_signal
              where first_seen_at >= now() - (%s || ' hours')::interval
                and (%s='all' or platform=%s)
            ),
            blackhorse as (
              select count(*)::int value
              from source_video
              where monitoring_priority >= 60
                and last_seen_at >= now() - (%s || ' hours')::interval
                and (%s='all' or platform=%s)
            ),
            l1 as (
              select count(*)::int value
              from source_video
              where research_level >= 1
                and last_seen_at >= now() - (%s || ' hours')::interval
                and (%s='all' or platform=%s)
            ),
            api as (
              select count(*)::int as call_records
              from external_api_call
              where started_at >= now() - (%s || ' hours')::interval
                and (%s='all' or platform=%s)
            )
            select
              (select value from hot) as new_hotspots,
              (select value from blackhorse) as blackhorse_candidates,
              (select value from l1) as entered_l1,
              (select call_records from api) as api_call_records
            """,
            (
                selected, selected2, hours, selected, selected2,
                hours, selected, selected2,
                hours, selected, selected2,
                hours, selected, selected2,
            ),
        )

        api_costs = _fetch_all(
            conn,
            """
            with calls as (
              select
                coalesce(cost_currency, 'UNKNOWN') as currency,
                estimated_cost,
                actual_cost,
                coalesce(metadata->>'cost_basis', '') as cost_basis,
                coalesce(metadata->>'billing_status', '') as billing_status
              from external_api_call
              where started_at >= now() - (%s || ' hours')::interval
                and (%s='all' or platform=%s)
            )
            select
              currency,
              coalesce(sum(case
                when cost_basis in ('estimated_unit_price', 'verified_unit_price')
                  and coalesce(estimated_cost, actual_cost) is not null
                  then coalesce(estimated_cost, actual_cost)
                else 0
              end), 0)::numeric as estimated_cost,
              coalesce(sum(case
                when cost_basis = 'supplier_bill' and actual_cost is not null then actual_cost
                else 0
              end), 0)::numeric as reconciled_cost,
              count(*) filter (where cost_basis in (
                'cache_zero', 'free_endpoint', 'nonbillable_http'
              ))::int as known_zero_calls,
              count(*) filter (where billing_status = 'unknown'
                or cost_basis not in (
                  'estimated_unit_price', 'verified_unit_price', 'supplier_bill',
                  'cache_zero', 'free_endpoint', 'nonbillable_http'
                ) or (cost_basis = 'supplier_bill' and actual_cost is null)
                or (cost_basis in ('estimated_unit_price', 'verified_unit_price')
                    and estimated_cost is null and actual_cost is null)
              )::int as unknown_cost_calls
            from calls
            group by currency
            order by currency
            """,
            (hours, selected, selected2),
        )

        blackhorse = _fetch_all(
            conn,
            """
            with latest_metric as (
              select
                m.video_id, m.play_count, m.like_count, m.comment_count,
                m.share_count, m.author_follower_count, m.captured_at
              from merged_video_metric m
            ),
            latest_score as (
              select distinct on (s.video_id)
                s.video_id, s.score, s.components, s.calculated_at
              from video_score s
              where s.score_type='priority'
              order by s.video_id, s.calculated_at desc, s.id desc
            ),
            source_hits as (
              select video_id,
                     array_agg(distinct source_type order by source_type) as sources,
                     count(distinct source_type)::int as source_count
              from discovery_event
              where discovered_at >= now() - interval '7 days'
              group by video_id
            )
            select
              v.id::text,
              v.platform,
              v.platform_video_id,
              coalesce(v.title, v.description, '(无标题)') as title,
              a.nickname as account_name,
              coalesce(s.score, v.monitoring_priority, 0)::numeric as priority,
              m.play_count, m.like_count, m.comment_count, m.share_count,
              m.author_follower_count,
              case when coalesce(m.author_follower_count,0) > 0
                then round(
                  100.0 * (
                    coalesce(m.like_count,0) +
                    coalesce(m.comment_count,0) +
                    coalesce(m.share_count,0)
                  ) / m.author_follower_count, 2
                )
                else null
              end as follower_efficiency,
              coalesce(h.sources, array[]::text[]) as sources,
              coalesce(h.source_count,0) as source_count,
              v.published_at,
              v.research_level,
              v.monitoring_status
            from source_video v
            left join source_account a on a.id=v.account_id
            left join latest_metric m on m.video_id=v.id
            left join latest_score s on s.video_id=v.id
            left join source_hits h on h.video_id=v.id
            where (%s='all' or v.platform=%s)
              and coalesce(s.score, v.monitoring_priority, 0) > 0
            order by coalesce(s.score, v.monitoring_priority, 0) desc,
                     v.last_seen_at desc
            limit 8
            """,
            (selected, selected2),
        )

        trend = _fetch_all(
            conn,
            """
            with days as (
              select generate_series(
                current_date - interval '6 days',
                current_date,
                interval '1 day'
              )::date as day
            ),
            counts as (
              select d.discovered_at::date as day, count(*)::int as value
              from discovery_event d
              join source_video v on v.id=d.video_id
              where d.discovered_at >= current_date - interval '6 days'
                and (%s='all' or v.platform=%s)
              group by d.discovered_at::date
            )
            select to_char(days.day, 'MM-DD') as day, coalesce(counts.value,0) as value
            from days
            left join counts using(day)
            order by days.day
            """,
            (selected, selected2),
        )

        keywords = _fetch_all(
            conn,
            """
            select title as keyword, count(*)::int as hits
            from external_signal
            where title is not null
              and btrim(title) <> ''
              and last_seen_at >= now() - interval '7 days'
              and (%s='all' or platform=%s)
            group by title
            order by hits desc, max(last_seen_at) desc
            limit 10
            """,
            (selected, selected2),
        )

        accounts = _fetch_all(
            conn,
            """
            with latest_account_metric as (
              select distinct on (account_id)
                account_id, follower_count, captured_at
              from account_metric_snapshot
              order by account_id, captured_at desc, id desc
            ),
            latest_score as (
              select distinct on (video_id) video_id, score
              from video_score
              where score_type='priority'
              order by video_id, calculated_at desc, id desc
            )
            select
              a.id::text,
              a.platform,
              a.nickname,
              m.follower_count,
              count(*) filter (where coalesce(s.score,0) >= 60)::int as blackhorse_count
            from source_account a
            left join latest_account_metric m on m.account_id=a.id
            left join source_video v on v.account_id=a.id
            left join latest_score s on s.video_id=v.id
            where (%s='all' or a.platform=%s)
            group by a.id, a.platform, a.nickname, m.follower_count
            order by blackhorse_count desc, m.follower_count desc nulls last
            limit 5
            """,
            (selected, selected2),
        )

        runtime = _fetch_one(
            conn,
            """
            with runs as (
              select
                count(*) filter (where status='running')::int as running,
                count(*) filter (where status='success')::int as success,
                coalesce(sum((summary->>'llm_calls')::int)
                  filter (where summary ? 'llm_calls'),0)::int as llm_calls
              from pipeline_run
              where started_at >= current_date
                and (%s='all' or platform=%s or platform is null)
            ),
            api as (
              select
                count(*)::int calls,
                count(*) filter (where status='error')::int errors
              from external_api_call
              where started_at >= current_date
                and (%s='all' or platform=%s)
            )
            select
              runs.running as running_jobs,
              runs.success as success_jobs,
              runs.llm_calls,
              api.calls as api_calls,
              api.errors as api_errors
            from runs, api
            """,
            (selected, selected2, selected, selected2),
        )

        history = _fetch_all(
            conn,
            """
            select
              id::text,
              to_char(started_at, 'MM-DD HH24:MI') as time,
              run_type,
              status,
              input_count,
              output_count,
              promoted_l1_count,
              summary
            from pipeline_run
            where (%s='all' or platform=%s or platform is null)
            order by started_at desc
            limit 5
            """,
            (selected, selected2),
        )

        # This is an account-level supplier bill, not a per-platform cost.  It
        # intentionally ignores the dashboard's selected platform.
        supplier_daily_spend = _daily_supplier_spend(conn, days=7)

    cases = []
    for item in blackhorse[:3]:
        score = float(item.get("priority") or 0)
        if int(item.get("research_level") or 0) >= 2:
            status = "已入选"
        elif score >= 80:
            status = "待跟进"
        else:
            status = "观察中"
        sources = item.get("sources") or []
        reason = (
            f"优先级 {score:.0f} · {len(sources)} 类发现来源"
            if sources
            else f"优先级 {score:.0f} · 等待更多证据"
        )
        cases.append({**item, "case_status": status, "reason": reason})

    return {
        "selected_platform": selected,
        "hours": hours,
        "platforms": platforms,
        "kpis": kpis,
        "api_costs": api_costs,
        "supplier_daily_spend": supplier_daily_spend,
        "blackhorse": blackhorse[:5],
        "trend": trend,
        "keywords": keywords,
        "cases": cases,
        "accounts": accounts,
        "runtime": runtime,
        "history": history,
        "ip_adaptation": {
            "status": "pending",
            "message": "首页暂未汇总 IP 适配建议。请在视频详情查看实际研究进度与结果；此处不生成未经证据支持的结论。"
        },
    }
