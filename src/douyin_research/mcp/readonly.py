"""Bounded, database-read-only canonical research queries for local MCP."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

_MAX_LIMIT = 50
_MAX_QUERY_CHARS = 120
_L3_LIST_FIELDS = (
    "narrative_structure",
    "hook_functions",
    "comment_semantics",
    "case_comparisons",
    "mechanism_hypotheses",
    "ip_fit",
    "limitations",
)


class MCPInputError(ValueError):
    """The caller supplied a value outside the bounded read-only contract."""


class CanonicalResearchQueries:
    """Query whitelisted canonical data using a read-only transaction.

    The class never returns transcript text, comment text, source URLs,
    provider payloads, task keys, fingerprints, annotations, credentials, or
    database errors.  It also intentionally has no methods that mutate state.
    """

    def __init__(self, dsn: str) -> None:
        if not isinstance(dsn, str) or not dsn:
            raise MCPInputError("a local MCP database URL is required")
        self._dsn = dsn

    def search_cases(
        self,
        *,
        query: str = "",
        platform: str | None = None,
        limit: int = 10,
    ) -> dict[str, object]:
        normalized_query = _query(query)
        normalized_platform = _platform(platform)
        bounded_limit = _limit(limit)
        with self._cursor() as cur:
            cur.execute(
                """
                with latest_metric as (
                  select
                    video_id, play_count, like_count, comment_count, share_count,
                    captured_at
                  from merged_video_metric
                )
                select
                  v.id::text as video_id,
                  v.platform,
                  coalesce(v.title, '(无标题)') as title,
                  a.nickname as account_name,
                  v.published_at,
                  v.duration_ms,
                  v.research_level,
                  v.availability_status,
                  m.play_count,
                  m.like_count,
                  m.comment_count,
                  m.share_count,
                  m.captured_at as metric_captured_at,
                  exists(
                    select 1 from research_promotion_decision p
                    where p.video_id=v.id and p.target_level=3
                      and p.outcome='selected'
                  ) as l3_selected
                from source_video v
                left join source_account a on a.id=v.account_id
                left join latest_metric m on m.video_id=v.id
                where (%s::text is null or v.platform=%s)
                  and (%s='' or coalesce(v.title, '') ilike '%%' || %s || '%%')
                order by v.published_at desc nulls last, v.last_seen_at desc
                limit %s
                """,
                (
                    normalized_platform,
                    normalized_platform,
                    normalized_query,
                    normalized_query,
                    bounded_limit,
                ),
            )
            rows = [_json_safe(dict(row)) for row in cur.fetchall()]
        return {"items": rows, "limit": bounded_limit, "read_only": True}

    def get_case_detail(self, *, video_id: str) -> dict[str, object]:
        normalized_id = _uuid(video_id)
        with self._cursor() as cur:
            cur.execute(
                """
                select
                  v.id::text as video_id,
                  v.platform,
                  coalesce(v.title, '(无标题)') as title,
                  a.nickname as account_name,
                  v.published_at,
                  v.duration_ms,
                  v.research_level,
                  v.availability_status,
                  v.monitoring_status,
                  v.monitoring_priority,
                  exists(
                    select 1 from research_promotion_decision p
                    where p.video_id=v.id and p.target_level=3
                      and p.outcome='selected'
                  ) as l3_selected
                from source_video v
                left join source_account a on a.id=v.account_id
                where v.id=%s
                """,
                (normalized_id,),
            )
            row = cur.fetchone()
            if row is None:
                raise MCPInputError("case does not exist")
            detail = _json_safe(dict(row))
            cur.execute(
                """
                select
                  a.model, a.model_revision, a.prompt_version, a.schema_version,
                  a.output, a.created_at,
                  c.api_cost, c.asr_cost, c.llm_cost, c.total_cost,
                  c.cost_currency, c.cost_basis
                from analysis_run a
                join research_task_cost c on c.id=a.task_cost_id
                where a.video_id=%s
                  and a.analysis_level='L3'
                  and a.analysis_type='l3_structured_research'
                  and a.status='completed'
                  and a.schema_version='l3-research-v1.0.0'
                  and a.output->>'privacy_reviewed'='true'
                  and c.status='completed'
                order by a.created_at desc, a.id desc
                limit 1
                """,
                (normalized_id,),
            )
            analysis = cur.fetchone()
        detail["l3_analysis"] = _public_l3_analysis(analysis) if analysis else None
        detail["read_only"] = True
        return detail

    def get_cost_summary(self, *, days: int = 30) -> dict[str, object]:
        bounded_days = _days(days)
        with self._cursor() as cur:
            cur.execute(
                """
                select
                  task_type,
                  status,
                  cost_currency,
                  count(*)::int as task_count,
                  sum(api_cost) as api_cost,
                  sum(asr_cost) as asr_cost,
                  sum(llm_cost) as llm_cost,
                  sum(total_cost) as total_cost
                from research_task_cost
                where created_at >= current_date - %s
                group by task_type, status, cost_currency
                order by task_type, status, cost_currency
                """,
                (bounded_days,),
            )
            task_costs = [_json_safe(dict(row)) for row in cur.fetchall()]
            cur.execute(
                """
                select
                  provider, account_scope, bill_scope_key, scope_kind, scope_label,
                  billing_date, cost_currency, billing_timezone, total_cost,
                  payable_cost, paid_cost, unpaid_cost, total_requests, paid_requests,
                  billing_finality, source_warning, fetched_at
                from supplier_daily_spend
                where billing_date >= current_date - %s
                order by billing_date desc, provider, account_scope, bill_scope_key
                limit 100
                """,
                (bounded_days,),
            )
            supplier_spend = [_json_safe(dict(row)) for row in cur.fetchall()]
            cur.execute(
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
                where started_at >= current_date - %s
                group by provider, coalesce(cost_currency, 'UNKNOWN')
                order by provider, currency
                """,
                (bounded_days,),
            )
            api_call_costs = [_json_safe(dict(row)) for row in cur.fetchall()]
            cur.execute(
                """
                select
                  budget_date,
                  provider,
                  budget_key,
                  max_cost,
                  max_requests,
                  spent_cost,
                  'known_estimated_reservation_not_supplier_bill'::text as spent_cost_basis,
                  unknown_price_requests,
                  used_requests,
                  cost_currency
                from daily_budget
                where budget_date >= current_date - %s
                order by budget_date desc, provider, budget_key
                limit 100
                """,
                (bounded_days,),
            )
            budgets = [_json_safe(dict(row)) for row in cur.fetchall()]
        return {
            "days": bounded_days,
            "task_costs": task_costs,
            "supplier_daily_spend": supplier_spend,
            "api_call_costs": api_call_costs,
            "daily_budgets": budgets,
            "actual_cost_source": "supplier_daily_spend",
            "estimate_source": "external_api_call_or_research_task_cost",
            "read_only": True,
        }

    def get_daily_briefing(
        self,
        *,
        platform: str | None = None,
        hours: int = 24,
        limit: int = 10,
    ) -> dict[str, object]:
        normalized_platform = _platform(platform)
        bounded_hours = _hours(hours)
        bounded_limit = _limit(limit)
        with self._cursor() as cur:
            cur.execute(
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
                  coalesce(v.title, '(无标题)') as title,
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
                where (%s::text is null or v.platform=%s)
                  and v.last_seen_at >= now() - (%s || ' hours')::interval
                  and v.availability_status='available'
                order by coalesce(s.score, v.monitoring_priority, 0) desc,
                         v.last_seen_at desc, v.id
                limit %s
                """,
                (
                    normalized_platform,
                    normalized_platform,
                    bounded_hours,
                    bounded_limit,
                ),
            )
            items = [_json_safe(dict(row)) for row in cur.fetchall()]
        return {
            "items": items,
            "platform": normalized_platform or "all",
            "hours": bounded_hours,
            "limit": bounded_limit,
            "interpretation": "rule_ranked_facts_not_model_generated_conclusions",
            "read_only": True,
        }

    def get_research_briefs(self, *, limit: int = 20) -> dict[str, object]:
        # The stdio MCP transport has no verified end-user identity. Research
        # briefs are user-owned; a shared read-only DB role must not list them.
        return {"ok": False, "error": "MCP_IDENTITY_SCOPE_UNAVAILABLE"}

    def _cursor(self):
        return _ReadOnlyCursor(self._dsn)


class _ReadOnlyCursor:
    """Open one explicit read-only transaction and close it after a query tool."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._conn = None
        self._cursor = None

    def __enter__(self):
        self._conn = psycopg.connect(self._dsn, row_factory=dict_row)
        try:
            self._cursor = self._conn.cursor()
            self._cursor.execute("begin read only")
            self._cursor.execute("set local lock_timeout = '1s'")
            self._cursor.execute("set local statement_timeout = '3s'")
            return self._cursor
        except Exception:
            try:
                self._conn.rollback()
            finally:
                self._conn.close()
            raise

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._conn is not None:
            try:
                self._conn.rollback()
            finally:
                self._conn.close()


def _limit(value: object) -> int:
    if isinstance(value, bool):
        raise MCPInputError("limit must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise MCPInputError("limit must be an integer") from None
    if not 1 <= result <= _MAX_LIMIT:
        raise MCPInputError(f"limit must be between 1 and {_MAX_LIMIT}")
    return result


def _days(value: object) -> int:
    if isinstance(value, bool):
        raise MCPInputError("days must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise MCPInputError("days must be an integer") from None
    if not 1 <= result <= 366:
        raise MCPInputError("days must be between 1 and 366")
    return result


def _hours(value: object) -> int:
    if isinstance(value, bool):
        raise MCPInputError("hours must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise MCPInputError("hours must be an integer") from None
    if not 1 <= result <= 720:
        raise MCPInputError("hours must be between 1 and 720")
    return result


def _query(value: object) -> str:
    if not isinstance(value, str):
        raise MCPInputError("query must be text")
    normalized = value.strip()
    if len(normalized) > _MAX_QUERY_CHARS or "\x00" in normalized:
        raise MCPInputError("query is invalid")
    return normalized


def _platform(value: object) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise MCPInputError("platform must be text")
    normalized = value.strip()
    if not normalized or len(normalized) > 64 or not normalized.replace("_", "").isalnum():
        raise MCPInputError("platform is invalid")
    return normalized


def _uuid(value: object) -> UUID:
    if not isinstance(value, str):
        raise MCPInputError("video_id must be a UUID")
    try:
        return UUID(value)
    except (TypeError, ValueError):
        raise MCPInputError("video_id must be a UUID") from None


def _public_l3_analysis(row: Mapping[str, Any]) -> dict[str, object]:
    output = row["output"]
    output_summary: dict[str, object] = {}
    if isinstance(output, Mapping):
        for field in _L3_LIST_FIELDS:
            values = output.get(field)
            if isinstance(values, list) and all(isinstance(value, str) for value in values):
                output_summary[f"{field}_count"] = min(len(values), 1000)
        if output.get("mechanism_hypotheses_are_inferences") is True:
            output_summary["mechanism_hypotheses_are_inferences"] = True
        if output.get("privacy_reviewed") is True:
            output_summary["privacy_reviewed"] = True
    return {
        "model": row["model"],
        "model_revision": row["model_revision"],
        "prompt_version": row["prompt_version"],
        "schema_version": row["schema_version"],
        "created_at": _json_safe(row["created_at"]),
        "output_summary": output_summary,
        "cost": {
            "api_cost": _json_safe(row["api_cost"]),
            "asr_cost": _json_safe(row["asr_cost"]),
            "llm_cost": _json_safe(row["llm_cost"]),
            "total_cost": _json_safe(row["total_cost"]),
            "currency": row["cost_currency"],
            "basis": row["cost_basis"],
        },
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def json_text(value: object) -> str:
    """Serialize one tool response without exposing Python reprs."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
