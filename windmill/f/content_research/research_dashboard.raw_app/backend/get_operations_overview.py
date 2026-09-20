"""Read-only task and cost overview for the dashboard's Admin/Developer view."""

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


def _json(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json(item) for item in value]
    return value


def _rows(cur, sql: str, args: tuple[Any, ...]) -> list[dict[str, Any]]:
    cur.execute(sql, args)
    return [_json(dict(row)) for row in cur.fetchall()]


def main(
    db: postgresql,
    platform: str = "all",
    days: int = 7,
    page: int = 1,
    page_size: int = 20,
):
    """Summarize safe operational facts without returning requests, output, or secrets."""

    platform = platform.strip() if isinstance(platform, str) else "all"
    days = max(1, min(int(days or 7), 90))
    page = max(1, int(page or 1))
    page_size = min(50, max(10, int(page_size or 20)))
    offset = (page - 1) * page_size
    connect_args = {
        "host": db["host"],
        "port": int(db.get("port", 5432)),
        "user": db["user"],
        "password": db["password"],
        "dbname": db["dbname"],
        "sslmode": db.get("sslmode", "prefer"),
    }
    scope = "(%s='all' or platform=%s)"
    with psycopg.connect(**connect_args) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("set transaction read only")
        api_summary = _rows(
            cur,
            f"""
            select
              count(*)::int as total_calls,
              count(*) filter (where status in ('success', 'completed'))::int as successful_calls,
              count(*) filter (where status not in ('success', 'completed'))::int as failed_calls,
              count(*) filter (where cached)::int as cache_hits
            from external_api_call
            where started_at >= now() - (%s || ' days')::interval
              and {scope}
            """,
            (days, platform, platform),
        )[0]
        # `actual_cost` is not, by itself, proof of a supplier settlement.  Old
        # TikHub rows used it for the published unit price, so treat that value
        # as an estimate unless the row explicitly says it was supplier-billed.
        api_costs = _rows(
            cur,
            f"""
            with calls as (
              select
                coalesce(cost_currency, 'UNKNOWN') as currency,
                estimated_cost,
                actual_cost,
                coalesce(metadata->>'cost_basis', '') as cost_basis,
                coalesce(metadata->>'billing_status', '') as billing_status
              from external_api_call
              where started_at >= now() - (%s || ' days')::interval
                and {scope}
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
            (days, platform, platform),
        )
        api_calls = _rows(
            cur,
            f"""
            select
              id::text as id,
              provider,
              platform,
              endpoint_key,
              status,
              http_status,
              cached,
              estimated_cost,
              actual_cost,
              cost_currency,
              started_at,
              finished_at,
              coalesce(metadata->>'cost_basis', 'unpriced') as cost_basis,
              metadata->>'price_source' as price_source,
              metadata->>'pricing_version' as pricing_version,
              case
                when metadata->>'http_attempt_count' ~ '^[0-9]+$'
                  then (metadata->>'http_attempt_count')::int
                else null
              end as http_attempt_count,
              case
                when metadata->>'unknown_attempt_count' ~ '^[0-9]+$'
                  then (metadata->>'unknown_attempt_count')::int
                else null
              end as unknown_attempt_count,
              case
                when coalesce(metadata->>'cost_basis', '') = 'supplier_bill'
                  and actual_cost is not null then 'reconciled'
                when coalesce(metadata->>'cost_basis', '') in (
                  'estimated_unit_price', 'verified_unit_price'
                ) and coalesce(estimated_cost, actual_cost) is not null then 'estimated'
                when coalesce(metadata->>'cost_basis', '') in (
                  'cache_zero', 'free_endpoint', 'nonbillable_http'
                ) then 'known_zero'
                else 'unknown'
              end as cost_status,
              case
                when coalesce(metadata->>'cost_basis', '') in ('estimated_unit_price', 'verified_unit_price')
                  and estimated_cost is null and actual_cost is null then 'unknown'
                when metadata->>'billing_status' in ('estimated', 'unknown', 'known_zero')
                  then metadata->>'billing_status'
                when coalesce(metadata->>'cost_basis', '') in ('estimated_unit_price', 'verified_unit_price')
                  then 'estimated'
                when coalesce(metadata->>'cost_basis', '') in ('cache_zero', 'free_endpoint', 'nonbillable_http')
                  then 'known_zero'
                else 'unknown'
              end as billing_status
            from external_api_call
            where started_at >= now() - (%s || ' days')::interval
              and {scope}
            order by started_at desc, id desc
            limit 50
            """,
            (days, platform, platform),
        )
        run_summary = _rows(
            cur,
            f"""
            select
              count(*)::int as total_runs,
              count(*) filter (where status='running')::int as running_runs,
              count(*) filter (where status in ('failed', 'error'))::int as failed_runs
            from pipeline_run
            where started_at >= now() - (%s || ' days')::interval
              and {scope}
            """,
            (days, platform, platform),
        )[0]
        task_costs = _rows(
            cur,
            """
            select
              c.cost_currency as currency,
              c.cost_basis as basis,
              count(*)::int as task_count,
              count(*) filter (where c.status='completed')::int as completed_count,
              count(*) filter (where c.status='failed')::int as failed_count,
              coalesce(sum(c.api_cost), 0)::numeric as api_cost,
              coalesce(sum(c.asr_cost), 0)::numeric as asr_cost,
              coalesce(sum(c.llm_cost), 0)::numeric as llm_cost,
              coalesce(sum(c.total_cost), 0)::numeric as known_total
            from research_task_cost c
            left join source_video v on v.id=c.video_id
            left join pipeline_run r on r.id=c.pipeline_run_id
            where c.created_at >= now() - (%s || ' days')::interval
              and (%s='all' or v.platform=%s or r.platform=%s)
            group by c.cost_currency, c.cost_basis
            order by c.cost_currency, c.cost_basis
            """,
            (days, platform, platform, platform),
        )
        cur.execute(
            """
            select count(*)::int as total
            from research_task_cost c
            left join source_video v on v.id=c.video_id
            left join pipeline_run r on r.id=c.pipeline_run_id
            where c.created_at >= now() - (%s || ' days')::interval
              and (%s='all' or v.platform=%s or r.platform=%s)
            """,
            (days, platform, platform, platform),
        )
        task_total = int(cur.fetchone()["total"])
        tasks = _rows(
            cur,
            """
            select
              c.id::text as id,
              c.task_type,
              c.task_version,
              c.status,
              c.cost_basis,
              c.api_cost,
              c.asr_cost,
              c.llm_cost,
              c.total_cost,
              c.cost_currency,
              c.created_at,
              v.id::text as video_id,
              coalesce(v.title, '(无标题视频)') as video_title,
              coalesce(v.platform, r.platform) as platform
            from research_task_cost c
            left join source_video v on v.id=c.video_id
            left join pipeline_run r on r.id=c.pipeline_run_id
            where c.created_at >= now() - (%s || ' days')::interval
              and (%s='all' or v.platform=%s or r.platform=%s)
            order by c.created_at desc, c.id desc
            limit %s offset %s
            """,
            (days, platform, platform, platform, page_size, offset),
        )
        runs = _rows(
            cur,
            f"""
            select
              id::text as id, run_type, run_version, platform, status,
              started_at, finished_at, input_count, output_count,
              promoted_l1_count, promoted_l2_count, promoted_l3_count,
              api_cost, asr_cost, llm_cost, cost_currency
            from pipeline_run
            where started_at >= now() - (%s || ' days')::interval
              and {scope}
            order by started_at desc, id desc
            limit 20
            """,
            (days, platform, platform),
        )
    return {
        "platform": platform,
        "days": days,
        "page": page,
        "page_size": page_size,
        "task_total": task_total,
        "api_summary": api_summary,
        "api_costs": api_costs,
        "api_calls": api_calls,
        "run_summary": run_summary,
        "task_costs": task_costs,
        "tasks": tasks,
        "runs": runs,
        "excluded_fields": [
            "request_fingerprint", "provider_request_id", "provider_task_ref",
            "input_fingerprint", "output_fingerprint", "metadata", "summary",
            "error_summary", "raw_provider_payload", "transcript_text",
        ],
        "readonly": True,
    }
