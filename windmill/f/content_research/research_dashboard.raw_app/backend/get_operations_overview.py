# py: ==3.14.*
"""Read-only task and cost overview for the dashboard's Admin/Developer view."""

from __future__ import annotations

import json
import os
import re
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


_ACTOR_RE = re.compile(r"^[^\s@]{1,128}@[^\s@]{1,120}$")
_LEGACY_ADMIN_ALLOWLIST_PATH = "f/content_research/research_action_writers"
_LEGACY_ACCESS_DENIED = "LEGACY_ADMIN_ACCESS_REQUIRED"


def _require_legacy_admin() -> str:
    """Require a Windmill-side admin before reading cross-project operations."""
    actor = os.environ.get("WM_END_USER_EMAIL", "").strip().lower()
    if not _ACTOR_RE.fullmatch(actor) or len(actor) > 254:
        raise PermissionError(_LEGACY_ACCESS_DENIED)
    try:
        import wmill

        raw = wmill.get_variable(_LEGACY_ADMIN_ALLOWLIST_PATH)
        values = json.loads(raw) if isinstance(raw, str) and raw.lstrip().startswith("[") else re.split(r"[,\n]", raw)
    except Exception:
        raise PermissionError(_LEGACY_ACCESS_DENIED) from None
    if not isinstance(values, list) or not any(
        isinstance(value, str)
        and _ACTOR_RE.fullmatch(value.strip().lower())
        and value.strip().lower() == actor
        for value in values
    ):
        raise PermissionError(_LEGACY_ACCESS_DENIED)
    return actor


def _json(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json(item) for item in value]
    return value


def _rows(cur, sql: str, args: tuple[Any, ...]) -> list[dict[str, Any]]:
    cur.execute(sql, args)
    return [_json(dict(row)) for row in cur.fetchall()]


def _daily_supplier_spend(cur, days: int) -> dict[str, Any]:
    """Return account-level supplier bills without attaching them to a platform.

    ``billing_date`` belongs to the supplier's stated timezone.  In particular,
    it must not be compared with the dashboard server's date (or Beijing's date)
    for US-billed providers such as TikHub.
    """
    cur.execute("select to_regclass('public.supplier_daily_spend') as table_name")
    if cur.fetchone()["table_name"] is None:
        return {
            "status": "not_synced",
            "records": [],
            "today": [],
            "message": "供应商日费用尚未同步。",
        }

    rows = _rows(
        cur,
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
    now = datetime.now(timezone.utc)
    for row in rows:
        timezone_name = row.get("billing_timezone") or "UTC"
        try:
            supplier_today = now.astimezone(ZoneInfo(timezone_name)).date().isoformat()
        except ZoneInfoNotFoundError:
            supplier_today = None
        fetched_at = row.get("fetched_at")
        try:
            fetched = datetime.fromisoformat(str(fetched_at).replace("Z", "+00:00"))
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
    current = [row for row in rows if row["period_status"] == "current_accumulating"]
    return {
        "status": "available" if rows else "not_synced",
        "records": rows,
        "today": current,
        "message": None if rows else "尚未同步到供应商日费用记录。",
    }


def main(
    db: postgresql,
    platform: str = "all",
    days: int = 7,
    page: int = 1,
    page_size: int = 20,
):
    """Summarize safe operational facts without returning requests, output, or secrets."""

    _require_legacy_admin()
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
              count(*) filter (where c.total_cost is null or c.cost_basis='unknown')::int as unknown_amount_count,
              coalesce(sum(c.api_cost), 0)::numeric as api_cost,
              coalesce(sum(c.asr_cost), 0)::numeric as asr_cost,
              coalesce(sum(c.llm_cost), 0)::numeric as llm_cost,
              coalesce(sum(
                coalesce(c.api_cost, 0) + coalesce(c.asr_cost, 0) + coalesce(c.llm_cost, 0)
              ) filter (where c.cost_basis <> 'unknown'), 0)::numeric as known_total
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
        # Supplier bills are account-level facts.  Deliberately do not apply the
        # platform selector used by the local call ledger above.
        supplier_daily_spend = _daily_supplier_spend(cur, days)
    return {
        "platform": platform,
        "days": days,
        "page": page,
        "page_size": page_size,
        "task_total": task_total,
        "api_summary": api_summary,
        "api_costs": api_costs,
        "supplier_daily_spend": supplier_daily_spend,
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
