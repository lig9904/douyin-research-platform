from __future__ import annotations

from datetime import datetime
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


def _params(platform: str, hours: int) -> tuple[str, str, int]:
    platform = (platform or "all").strip()
    hours = max(1, min(int(hours or 24), 24 * 30))
    return platform, platform, hours


def _json(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
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
              select
                coalesce(sum(actual_cost) filter (where cached=false), 0)::numeric as cost,
                count(*) filter (where cached=false)::int as requests
              from external_api_call
              where started_at >= now() - (%s || ' hours')::interval
                and (%s='all' or platform=%s)
            ),
            budget as (
              select
                case
                  when coalesce(sum(max_cost),0) > 0
                    then least(100, 100 * coalesce(sum(spent_cost),0) / sum(max_cost))
                  when coalesce(sum(max_requests),0) > 0
                    then least(100, 100.0 * coalesce(sum(used_requests),0) / sum(max_requests))
                  else 0
                end::numeric as usage
              from daily_budget
              where budget_date=current_date
            )
            select
              (select value from hot) as new_hotspots,
              (select value from blackhorse) as blackhorse_candidates,
              (select value from l1) as entered_l1,
              (select cost from api) as api_cost_usd,
              (select requests from api) as api_requests,
              (select usage from budget) as budget_usage_pct
            """,
            (
                selected, selected2, hours, selected, selected2,
                hours, selected, selected2,
                hours, selected, selected2,
                hours, selected, selected2,
            ),
        )

        blackhorse = _fetch_all(
            conn,
            """
            with latest_metric as (
              select distinct on (m.video_id)
                m.video_id, m.play_count, m.like_count, m.comment_count,
                m.share_count, m.author_follower_count, m.captured_at
              from metric_snapshot m
              order by m.video_id, m.captured_at desc, m.id desc
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
        "blackhorse": blackhorse[:5],
        "trend": trend,
        "keywords": keywords,
        "cases": cases,
        "accounts": accounts,
        "runtime": runtime,
        "history": history,
        "ip_adaptation": {
            "status": "pending",
            "message": "L2/L3 尚未启用。首页不生成未经研究支持的 IP 适配结论。"
        },
    }
