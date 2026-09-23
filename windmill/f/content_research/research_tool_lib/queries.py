# py: ==3.14.*
"""Shared, fail-closed query layer for Windmill's read-only MCP tools.

This file deliberately has no ``main`` function, so Windmill treats it as
shared logic rather than an independently runnable tool.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any, TypeVar
from uuid import UUID

import psycopg
import wmill
from psycopg.rows import dict_row


MAX_LIMIT = 50
MAX_QUERY_CHARS = 120
MAX_DAYS = 366
MAX_HOURS = 720
T = TypeVar("T")
# MCP tools use a separate database identity.  The main ``research_db``
# resource is intentionally reserved for collectors, reviews and other
# application paths that need writes.
RESEARCH_DB_RESOURCE = "f/content_research/research_db_readonly"
L3_LIST_FIELDS = (
    "narrative_structure",
    "hook_functions",
    "comment_semantics",
    "case_comparisons",
    "mechanism_hypotheses",
    "ip_fit",
    "limitations",
)


class ToolInputError(ValueError):
    """The caller supplied input outside the public MCP contract."""


def safe_tool(call: Callable[[], T]) -> T | dict[str, object]:
    """Return fixed errors without leaking SQL, credentials or query content."""

    try:
        return call()
    except ToolInputError:
        return {"ok": False, "error": "MCP_INPUT_INVALID"}
    except Exception:
        return {"ok": False, "error": "MCP_READ_UNAVAILABLE"}


def research_db() -> Mapping[str, object]:
    """Load the fixed server-side resource without exposing it as a tool input."""

    value = wmill.get_resource(RESEARCH_DB_RESOURCE)
    if not isinstance(value, Mapping):
        raise RuntimeError("research database resource is unavailable")
    required = {"host", "user", "password", "dbname"}
    if not required.issubset(value):
        raise RuntimeError("research database resource is incomplete")
    return value


def validate_limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolInputError("limit must be an integer")
    if not 1 <= value <= MAX_LIMIT:
        raise ToolInputError("limit is outside the allowed range")
    return value


def validate_days(value: object, *, maximum: int = MAX_DAYS) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolInputError("days must be an integer")
    if not 1 <= value <= maximum:
        raise ToolInputError("days is outside the allowed range")
    return value


def validate_hours(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolInputError("hours must be an integer")
    if not 1 <= value <= MAX_HOURS:
        raise ToolInputError("hours is outside the allowed range")
    return value


def validate_query(value: object) -> str:
    if not isinstance(value, str):
        raise ToolInputError("query must be text")
    normalized = value.strip()
    if len(normalized) > MAX_QUERY_CHARS or "\x00" in normalized:
        raise ToolInputError("query is invalid")
    return normalized


def validate_platform(value: object) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ToolInputError("platform must be text")
    normalized = value.strip()
    if not normalized or len(normalized) > 64:
        raise ToolInputError("platform is invalid")
    if not normalized.replace("_", "").isalnum():
        raise ToolInputError("platform is invalid")
    return normalized


def validate_uuid(value: object, *, field: str) -> UUID:
    if not isinstance(value, str):
        raise ToolInputError(f"{field} must be a UUID")
    try:
        return UUID(value)
    except (TypeError, ValueError):
        raise ToolInputError(f"{field} must be a UUID") from None


def validate_score(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolInputError("min_score must be numeric")
    score = Decimal(str(value))
    if not score.is_finite() or score < 0 or score > 100:
        raise ToolInputError("min_score is outside the allowed range")
    return score


def search_cases(
    db: Mapping[str, object],
    *,
    query: object = "",
    platform: object = "",
    limit: object = 10,
) -> dict[str, object]:
    normalized_query = validate_query(query)
    normalized_platform = validate_platform(platform)
    bounded_limit = validate_limit(limit)
    rows = _all(
        db,
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
            where p.video_id=v.id and p.target_level=3 and p.outcome='selected'
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
    return _ok(items=rows, limit=bounded_limit)


def get_case_detail(db: Mapping[str, object], *, video_id: object) -> dict[str, object]:
    normalized_id = validate_uuid(video_id, field="video_id")
    with _read_cursor(db) as cur:
        cur.execute(
            """
            with latest_metric as (
              select
                video_id, play_count, like_count, comment_count, share_count,
                collect_count, author_follower_count, captured_at
              from merged_video_metric
              where video_id=%s
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
              v.monitoring_status,
              v.monitoring_priority,
              m.play_count,
              m.like_count,
              m.comment_count,
              m.share_count,
              m.collect_count,
              m.author_follower_count,
              m.captured_at as metric_captured_at,
              exists(
                select 1 from research_promotion_decision p
                where p.video_id=v.id and p.target_level=3 and p.outcome='selected'
              ) as l3_selected
            from source_video v
            left join source_account a on a.id=v.account_id
            left join latest_metric m on m.video_id=v.id
            where v.id=%s
            """,
            (normalized_id, normalized_id),
        )
        row = cur.fetchone()
        if row is None:
            raise ToolInputError("case does not exist")
        detail = _json(dict(row))
        cur.execute(
            """
            select
              a.model, a.model_revision, a.prompt_version, a.schema_version,
              a.created_at,
              case when jsonb_typeof(a.output->'narrative_structure')='array'
                then least(jsonb_array_length(a.output->'narrative_structure'), 1000) end
                as narrative_structure_count,
              case when jsonb_typeof(a.output->'hook_functions')='array'
                then least(jsonb_array_length(a.output->'hook_functions'), 1000) end
                as hook_functions_count,
              case when jsonb_typeof(a.output->'comment_semantics')='array'
                then least(jsonb_array_length(a.output->'comment_semantics'), 1000) end
                as comment_semantics_count,
              case when jsonb_typeof(a.output->'case_comparisons')='array'
                then least(jsonb_array_length(a.output->'case_comparisons'), 1000) end
                as case_comparisons_count,
              case when jsonb_typeof(a.output->'mechanism_hypotheses')='array'
                then least(jsonb_array_length(a.output->'mechanism_hypotheses'), 1000) end
                as mechanism_hypotheses_count,
              case when jsonb_typeof(a.output->'ip_fit')='array'
                then least(jsonb_array_length(a.output->'ip_fit'), 1000) end
                as ip_fit_count,
              case when jsonb_typeof(a.output->'limitations')='array'
                then least(jsonb_array_length(a.output->'limitations'), 1000) end
                as limitations_count,
              (a.output->>'mechanism_hypotheses_are_inferences')='true'
                as mechanism_hypotheses_are_inferences,
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
              and c.task_type='l3_structured_research'
              and c.task_version='l3-research-v1.0.0'
              and c.video_id=a.video_id
              and c.input_fingerprint=a.input_fingerprint
              and c.output_fingerprint=a.output_fingerprint
            order by a.created_at desc, a.id desc
            limit 1
            """,
            (normalized_id,),
        )
        analysis = cur.fetchone()
    detail["l3_analysis"] = _public_l3(analysis) if analysis else None
    detail["ok"] = True
    detail["read_only"] = True
    return detail


def get_hot_videos(
    db: Mapping[str, object],
    *,
    platform: object = "",
    days: object = 7,
    limit: object = 10,
) -> dict[str, object]:
    normalized_platform = validate_platform(platform)
    bounded_days = validate_days(days, maximum=90)
    bounded_limit = validate_limit(limit)
    rows = _all(
        db,
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
          v.published_at,
          v.duration_ms,
          v.research_level,
          m.play_count,
          m.like_count,
          m.comment_count,
          m.share_count,
          m.captured_at as metric_captured_at
        from source_video v
        join latest_metric m on m.video_id=v.id
        where (%s::text is null or v.platform=%s)
          and v.published_at >= now() - (%s || ' days')::interval
          and v.availability_status='available'
        order by m.play_count desc nulls last,
                 m.like_count desc nulls last,
                 v.published_at desc nulls last
        limit %s
        """,
        (normalized_platform, normalized_platform, bounded_days, bounded_limit),
    )
    return _ok(items=rows, days=bounded_days, limit=bounded_limit)


def get_blackhorse_videos(
    db: Mapping[str, object],
    *,
    platform: object = "",
    min_score: object = 60,
    limit: object = 10,
) -> dict[str, object]:
    normalized_platform = validate_platform(platform)
    threshold = validate_score(min_score)
    bounded_limit = validate_limit(limit)
    rows = _all(
        db,
        """
        with latest_score as (
          select distinct on (video_id)
            video_id, score, rule_version, calculated_at
          from video_score
          where score_type='priority'
          order by video_id, calculated_at desc, id desc
        ), latest_metric as (
          select
            video_id, play_count, like_count, comment_count, share_count,
            author_follower_count, captured_at
          from merged_video_metric
        )
        select
          v.id::text as video_id,
          v.platform,
          v.published_at,
          v.duration_ms,
          v.research_level,
          s.score,
          s.rule_version,
          s.calculated_at as score_calculated_at,
          m.play_count,
          m.like_count,
          m.comment_count,
          m.share_count,
          m.author_follower_count,
          m.captured_at as metric_captured_at
        from source_video v
        join latest_score s on s.video_id=v.id
        left join latest_metric m on m.video_id=v.id
        where (%s::text is null or v.platform=%s)
          and s.score >= %s
          and v.availability_status='available'
        order by s.score desc, s.calculated_at desc, v.id
        limit %s
        """,
        (normalized_platform, normalized_platform, threshold, bounded_limit),
    )
    return _ok(items=rows, min_score=threshold, limit=bounded_limit)


def search_accounts(
    db: Mapping[str, object],
    *,
    query: object = "",
    platform: object = "",
    limit: object = 10,
) -> dict[str, object]:
    normalized_query = validate_query(query)
    normalized_platform = validate_platform(platform)
    bounded_limit = validate_limit(limit)
    rows = _all(
        db,
        """
        with latest_metric as (
          select distinct on (account_id)
            account_id, follower_count, following_count, total_favorited,
            video_count, captured_at
          from account_metric_snapshot
          order by account_id, captured_at desc, id desc
        ), domains as (
          select account_id,
                 array_agg(distinct tag_value order by tag_value)
                   filter (where tag_type='content_domain') as content_domains
          from account_tag
          group by account_id
        )
        select
          a.id::text as account_id,
          a.platform,
          a.platform_account_id,
          a.research_level,
          a.monitoring_status,
          a.monitoring_priority,
          m.follower_count,
          m.following_count,
          m.total_favorited,
          m.video_count,
          m.captured_at as metric_captured_at,
          coalesce(d.content_domains, array[]::text[]) as content_domains
        from source_account a
        left join latest_metric m on m.account_id=a.id
        left join domains d on d.account_id=a.id
        where (%s::text is null or a.platform=%s)
          and (
            %s=''
            or coalesce(a.nickname, '') ilike '%%' || %s || '%%'
            or coalesce(a.platform_account_id, '') ilike '%%' || %s || '%%'
          )
        order by m.follower_count desc nulls last, a.last_seen_at desc
        limit %s
        """,
        (
            normalized_platform,
            normalized_platform,
            normalized_query,
            normalized_query,
            normalized_query,
            bounded_limit,
        ),
    )
    return _ok(items=rows, limit=bounded_limit)


def get_account_videos(
    db: Mapping[str, object],
    *,
    account_id: object,
    days: object = 90,
    limit: object = 20,
) -> dict[str, object]:
    normalized_id = validate_uuid(account_id, field="account_id")
    bounded_days = validate_days(days)
    bounded_limit = validate_limit(limit)
    rows = _all(
        db,
        """
        with latest_metric as (
          select
            video_id, play_count, like_count, comment_count, share_count,
            collect_count, captured_at
          from merged_video_metric
        ), latest_score as (
          select distinct on (video_id)
            video_id, score, rule_version, calculated_at
          from video_score
          where score_type='priority'
          order by video_id, calculated_at desc, id desc
        )
        select
          v.id::text as video_id,
          v.platform,
          v.published_at,
          v.duration_ms,
          v.research_level,
          v.availability_status,
          m.play_count,
          m.like_count,
          m.comment_count,
          m.share_count,
          m.collect_count,
          m.captured_at as metric_captured_at,
          s.score,
          s.rule_version
        from source_video v
        left join latest_metric m on m.video_id=v.id
        left join latest_score s on s.video_id=v.id
        where v.account_id=%s
          and v.last_seen_at >= now() - (%s || ' days')::interval
        order by v.published_at desc nulls last, v.last_seen_at desc
        limit %s
        """,
        (normalized_id, bounded_days, bounded_limit),
    )
    return _ok(
        account_id=str(normalized_id),
        items=rows,
        days=bounded_days,
        limit=bounded_limit,
    )


def get_metric_history(
    db: Mapping[str, object],
    *,
    video_id: object,
    days: object = 30,
    limit: object = 50,
) -> dict[str, object]:
    normalized_id = validate_uuid(video_id, field="video_id")
    bounded_days = validate_days(days, maximum=90)
    bounded_limit = validate_limit(limit)
    rows = _all(
        db,
        """
        select
          captured_at,
          play_count,
          like_count,
          comment_count,
          share_count,
          collect_count,
          author_follower_count
        from metric_snapshot
        where video_id=%s
          and captured_at >= now() - (%s || ' days')::interval
        order by captured_at desc, id desc
        limit %s
        """,
        (normalized_id, bounded_days, bounded_limit),
    )
    return _ok(
        video_id=str(normalized_id),
        items=rows,
        days=bounded_days,
        limit=bounded_limit,
    )


def get_daily_briefing(
    db: Mapping[str, object],
    *,
    platform: object = "",
    hours: object = 24,
    limit: object = 10,
) -> dict[str, object]:
    """Return a bounded facts-first briefing with public metadata only."""

    normalized_platform = validate_platform(platform)
    bounded_hours = validate_hours(hours)
    bounded_limit = validate_limit(limit)
    rows = _all(
        db,
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
    return _ok(
        items=rows,
        platform=normalized_platform or "all",
        hours=bounded_hours,
        limit=bounded_limit,
        interpretation="rule_ranked_facts_not_model_generated_conclusions",
    )


def get_research_briefs(
    db: Mapping[str, object], *, limit: object = 20
) -> dict[str, object]:
    # Gateway script tokens are workspace-scoped, not mapped to a verified
    # actor. These briefs are private to their owner, so fail before DB access.
    return {"ok": False, "error": "MCP_IDENTITY_SCOPE_UNAVAILABLE"}


def get_cost_summary(
    db: Mapping[str, object], *, days: object = 30
) -> dict[str, object]:
    bounded_days = validate_days(days)
    with _read_cursor(db) as cur:
        cur.execute(
            """
            select cost_currency as currency, cost_basis,
                   count(*)::int as task_count,
                   count(*) filter (where status='completed')::int as completed_count,
                   coalesce(sum(api_cost), 0)::numeric as api_cost,
                   coalesce(sum(asr_cost), 0)::numeric as asr_cost,
                   coalesce(sum(llm_cost), 0)::numeric as llm_cost,
                   coalesce(sum(total_cost), 0)::numeric as known_total
            from research_task_cost
            where created_at >= current_date - %s
            group by cost_currency, cost_basis
            order by cost_currency, cost_basis
            """,
            (bounded_days,),
        )
        task_costs = [_json(dict(row)) for row in cur.fetchall()]
        cur.execute(
            """
            select provider, account_scope, bill_scope_key, scope_kind, scope_label,
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
        supplier_spend = [_json(dict(row)) for row in cur.fetchall()]
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
        api_calls = [_json(dict(row)) for row in cur.fetchall()]
    return _ok(
        days=bounded_days,
        task_costs=task_costs,
        supplier_daily_spend=supplier_spend,
        api_call_costs=api_calls,
        actual_cost_source="supplier_daily_spend",
        estimate_source="external_api_call_or_research_task_cost",
    )


def _ok(**values: object) -> dict[str, object]:
    return {"ok": True, "read_only": True, **_json(values)}


class _read_cursor:
    def __init__(self, db: Mapping[str, object]) -> None:
        self._db = db
        self._conn = None
        self._cursor = None

    def __enter__(self):
        self._conn = psycopg.connect(
            host=str(self._db["host"]),
            port=int(self._db.get("port", 5432)),
            user=str(self._db["user"]),
            password=str(self._db["password"]),
            dbname=str(self._db["dbname"]),
            sslmode=str(self._db.get("sslmode", "prefer")),
            row_factory=dict_row,
        )
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


def _all(
    db: Mapping[str, object], sql: str, params: tuple[object, ...]
) -> list[dict[str, Any]]:
    with _read_cursor(db) as cur:
        cur.execute(sql, params)
        return [_json(dict(row)) for row in cur.fetchall()]


def _public_l3(row: Mapping[str, object]) -> dict[str, object]:
    output_summary: dict[str, object] = {"privacy_reviewed": True}
    for field in L3_LIST_FIELDS:
        count = row.get(f"{field}_count")
        if isinstance(count, int) and not isinstance(count, bool) and 0 <= count <= 1000:
            output_summary[f"{field}_count"] = count
    if row.get("mechanism_hypotheses_are_inferences") is True:
        output_summary["mechanism_hypotheses_are_inferences"] = True
    return _json(
        {
            "model": row.get("model"),
            "model_revision": row.get("model_revision"),
            "prompt_version": row.get("prompt_version"),
            "schema_version": row.get("schema_version"),
            "created_at": row.get("created_at"),
            "output_summary": output_summary,
            "cost": {
                "api": row.get("api_cost"),
                "asr": row.get("asr_cost"),
                "llm": row.get("llm_cost"),
                "total": row.get("total_cost"),
                "currency": row.get("cost_currency"),
                "basis": row.get("cost_basis"),
            },
        }
    )


def _json(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    return value
