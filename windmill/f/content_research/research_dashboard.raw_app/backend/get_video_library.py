# py: ==3.14.*
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from decimal import Decimal
from typing import Any, TypedDict
from uuid import UUID

import psycopg
from psycopg.rows import dict_row


_ACTOR_RE = re.compile(r"^[^\s@]{1,128}@[^\s@]{1,120}$")
_ACCESS_DENIED = "RESEARCH_VIDEO_ACCESS_DENIED"


class postgresql(TypedDict):
    host: str
    port: int
    user: str
    password: str
    dbname: str
    sslmode: str


def _actor() -> str:
    value = os.environ.get("WM_END_USER_EMAIL", "").strip().lower()
    if not _ACTOR_RE.fullmatch(value) or len(value) > 254:
        raise PermissionError(_ACCESS_DENIED)
    return value


def _project_id(value: object) -> UUID | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("project_id must be a UUID")
    try:
        return UUID(value)
    except ValueError:
        raise ValueError("project_id must be a UUID") from None


def _legacy_reader_allowed(actor: str, allowlist: str | None) -> bool:
    if not isinstance(allowlist, str) or not allowlist.strip():
        return False
    source = allowlist.strip()
    try:
        values = json.loads(source) if source.startswith("[") else re.split(r"[,\n]", source)
    except json.JSONDecodeError:
        return False
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        return False
    return actor in {
        value.strip().lower() for value in values
        if _ACTOR_RE.fullmatch(value.strip().lower())
    }


def _get_legacy_allowlist() -> str:
    """Read the server-owned legacy reader list; never trust caller input."""
    import wmill

    return wmill.get_variable("f/content_research/research_action_writers")


def _can_read_project(cur, *, project_id: UUID, actor: str) -> bool:
    cur.execute(
        "select project_actor_can_read(%s::uuid, %s) as allowed",
        (project_id, actor),
    )
    row = cur.fetchone()
    return bool(row and row["allowed"])


def _can_read_project_video(
    cur, *, project_id: UUID, actor: str, video_id: UUID,
) -> bool:
    cur.execute(
        "select project_video_can_read(%s::uuid, %s, %s::uuid) as allowed",
        (project_id, actor, video_id),
    )
    row = cur.fetchone()
    return bool(row and row["allowed"])


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


def _legacy_analysis_row(
    conn, *, project_id: UUID | None, sql: str, args=(),
) -> dict[str, Any]:
    """Never reuse globally scoped ASR/L3 records in a project view."""
    if project_id is not None:
        return {}
    return _fetch_one(conn, sql, args)


def _connect(db: postgresql):
    args: dict[str, Any] = {
        "host": db["host"],
        "port": int(db.get("port", 5432)),
        "user": db["user"],
        "password": db["password"],
        "dbname": db["dbname"],
        "sslmode": db.get("sslmode", "prefer"),
    }
    if db.get("options"):
        args["options"] = db["options"]
    return psycopg.connect(**args)


_L3_OUTPUT_LIST_FIELDS = (
    "narrative_structure",
    "hook_functions",
    "comment_semantics",
    "case_comparisons",
    "mechanism_hypotheses",
    "ip_fit",
    "limitations",
)
_L3_OUTPUT_BOOL_FIELDS = (
    "mechanism_hypotheses_are_inferences",
    "privacy_reviewed",
)
_L3_ANALYSIS_TYPE = "l3_structured_research"
_L3_SCHEMA_VERSION = "l3-research-v1.0.0"
_ASR_TRANSCRIPT_TEXT_LIMIT = 12_000


def _public_l3_output(output: Any) -> dict[str, Any]:
    """Return only the documented, display-safe L3 result fields."""
    if not isinstance(output, dict):
        return {}
    public: dict[str, Any] = {}
    for field in _L3_OUTPUT_LIST_FIELDS:
        value = output.get(field)
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            public[field] = value
    for field in _L3_OUTPUT_BOOL_FIELDS:
        value = output.get(field)
        if type(value) is bool:
            public[field] = value
    return public


def _public_asr_transcript(row: dict[str, Any]) -> dict[str, Any]:
    """Return the bounded transcript display contract, excluding execution internals."""

    return {
        "transcript_id": row.get("transcript_id"),
        "text": row["text"],
        "truncated": row["truncated"],
        "quality_status": row["quality_status"],
        "provider": row["asr_provider"],
        "model_id": row["model_id"],
        "model_revision": row["model_revision"],
        "engine_version": row["engine_version"],
        "language": row["language"],
        "audio_duration_ms": row["audio_duration_ms"],
        "created_at": row["created_at"],
        "cost": {
            "api_cost": row["api_cost"],
            "asr_cost": row["asr_cost"],
            "llm_cost": row["llm_cost"],
            "total_cost": row["total_cost"],
            "currency": row["cost_currency"],
            "basis": row["cost_basis"],
        },
    }


def main(
    db: postgresql,
    platform: str = "douyin",
    days: int = 30,
    research_level: int = -1,
    source_type: str = "all",
    priority_min: float = -1,
    status: str = "all",
    play_min: int = -1,
    play_max: int = -1,
    follower_min: int = -1,
    follower_max: int = -1,
    collected: str = "all",
    query: str = "",
    page: int = 1,
    page_size: int = 10,
    sort: str = "published_desc",
    selected_video_id: str = "",
    project_id: str | None = None,
):
    platform = (platform or "all").strip()
    days = max(1, min(int(days or 30), 365))
    research_level = int(research_level)
    priority_min = float(priority_min)
    play_min = int(play_min)
    play_max = int(play_max)
    follower_min = int(follower_min)
    follower_max = int(follower_max)
    page = max(1, int(page or 1))
    page_size = min(50, max(10, int(page_size or 10)))
    query = (query or "").strip()
    scoped_project_id = _project_id(project_id)
    actor = _actor()

    sort_map = {
        "published_desc": "v.published_at desc nulls last, v.last_seen_at desc",
        "priority_desc": "priority desc, v.last_seen_at desc",
        "likes_desc": "m.like_count desc nulls last, v.last_seen_at desc",
        "plays_desc": "m.play_count desc nulls last, v.last_seen_at desc",
    }
    if scoped_project_id is not None:
        sort_map = {
            "published_desc": "v.published_at desc nulls last, inclusion_row.last_seen_at desc",
            "likes_desc": "m.like_count desc nulls last, inclusion_row.last_seen_at desc",
            "plays_desc": "m.play_count desc nulls last, inclusion_row.last_seen_at desc",
        }
    order_by = sort_map.get(sort, sort_map["published_desc"])

    where_sql = """
      (%s='all' or v.platform=%s)
      and (v.last_seen_at >= now() - (%s || ' days')::interval
           or v.platform_video_id=%s)
      and (%s < 0 or v.research_level=%s)
      and (%s < 0 or coalesce(s.score, v.monitoring_priority, 0) >= %s)
      and (%s='all' or v.monitoring_status=%s)
      and (%s < 0 or (m.play_count is not null and m.play_count >= %s))
      and (%s < 0 or (m.play_count is not null and m.play_count <= %s))
      and (%s < 0 or (m.author_follower_count is not null and m.author_follower_count >= %s))
      and (%s < 0 or (m.author_follower_count is not null and m.author_follower_count <= %s))
      and (
        %s='all'
        or (%s='yes' and coalesce(c.collection_count,0)>0)
        or (%s='no' and coalesce(c.collection_count,0)=0)
      )
      and (
        %s=''
        or coalesce(v.title,'') ilike '%%' || %s || '%%'
        or coalesce(v.description,'') ilike '%%' || %s || '%%'
        or coalesce(a.nickname,'') ilike '%%' || %s || '%%'
        or v.platform_video_id=%s
      )
      and (
        %s='all'
        or exists (
          select 1 from discovery_event ds
          where ds.video_id=v.id and ds.source_type=%s
        )
      )
    """
    args = (
        platform, platform, days, query,
        research_level, research_level,
        priority_min, priority_min,
        status, status,
        play_min, play_min,
        play_max, play_max,
        follower_min, follower_min,
        follower_max, follower_max,
        collected, collected, collected,
        query, query, query, query, query,
        source_type, source_type,
    )

    # Project UI intentionally hides the legacy recency selector. Return all
    # non-archived project inclusions; a hidden 30-day cutoff would make older
    # accepted evidence disappear even when the project member searches for it.
    scoped_where_sql = """
      (%s='all' or v.platform=%s)
      and (%s < 0 or (m.play_count is not null and m.play_count >= %s))
      and (%s < 0 or (m.play_count is not null and m.play_count <= %s))
      and (%s < 0 or (m.author_follower_count is not null and m.author_follower_count >= %s))
      and (%s < 0 or (m.author_follower_count is not null and m.author_follower_count <= %s))
      and (
        %s=''
        or coalesce(v.title,'') ilike '%%' || %s || '%%'
        or coalesce(v.description,'') ilike '%%' || %s || '%%'
        or coalesce(a.nickname,'') ilike '%%' || %s || '%%'
        or v.platform_video_id=%s
      )
      and (%s='all' or inclusion_row.source_type=%s)
    """
    scoped_args = (
        platform, platform,
        play_min, play_min,
        play_max, play_max,
        follower_min, follower_min,
        follower_max, follower_max,
        query, query, query, query, query,
        source_type, source_type,
    )

    base_cte = """
    with latest_metric as (
      select * from merged_video_metric
    ),
    latest_score as (
      select distinct on (video_id)
        video_id, score, components, calculated_at
      from video_score
      where score_type='priority'
      order by video_id, calculated_at desc, id desc
    ),
    source_hits as (
      select video_id,
             array_agg(distinct source_type order by source_type) as sources,
             count(distinct source_type)::int as source_count
      from discovery_event
      group by video_id
    ),
    collections as (
      select video_id, count(distinct collection_id)::int as collection_count
      from collection_item
      where video_id is not null
      group by video_id
    )
    """

    scope_join = ""
    scope_join_args: tuple[object, ...] = ()
    active_where_sql = where_sql
    active_args = args
    total_private_joins = """
            left join latest_score s on s.video_id=v.id
            left join collections c on c.video_id=v.id
    """
    item_private_joins = """
            left join latest_score s on s.video_id=v.id
            left join source_hits h on h.video_id=v.id
            left join collections c on c.video_id=v.id
    """
    item_research_fields = """
              v.research_level,
              v.monitoring_status,
              v.monitoring_priority,
    """
    item_account_id_field = "a.id::text as account_id,"
    item_project_status_field = "null::text as project_inclusion_status,"
    item_priority_field = "coalesce(s.score, v.monitoring_priority, 0)::numeric as priority,"
    item_source_fields = """
              coalesce(h.sources, array[]::text[]) as sources,
              coalesce(h.source_count,0) as source_count,
              coalesce(c.collection_count,0) as collection_count
    """
    if scoped_project_id is not None:
        if (
            research_level != -1
            or priority_min != -1
            or status != "all"
            or collected != "all"
            or sort == "priority_desc"
        ):
            raise PermissionError(_ACCESS_DENIED)
        scope_join = """
            join project_video_inclusion inclusion_row
              on inclusion_row.video_id=v.id
             and inclusion_row.project_id=%s::uuid
             and inclusion_row.status <> 'archived'
        """
        scope_join_args = (scoped_project_id,)
        active_where_sql = scoped_where_sql
        active_args = scoped_args
        total_private_joins = ""
        item_private_joins = ""
        item_research_fields = """
              null::integer as research_level,
              null::text as monitoring_status,
              null::numeric as monitoring_priority,
        """
        item_account_id_field = "null::text as account_id,"
        item_project_status_field = "inclusion_row.status as project_inclusion_status,"
        item_priority_field = "null::numeric as priority,"
        item_source_fields = """
              array[inclusion_row.source_type] as sources,
              1::int as source_count,
              null::int as collection_count
        """

    with _connect(db) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("set transaction isolation level repeatable read read only")
            if scoped_project_id is None:
                try:
                    legacy_allowed = _legacy_reader_allowed(actor, _get_legacy_allowlist())
                except Exception:
                    legacy_allowed = False
                if not legacy_allowed:
                    raise PermissionError(_ACCESS_DENIED)
            elif not _can_read_project(
                cur, project_id=scoped_project_id, actor=actor,
            ):
                # Do not distinguish a missing, paused, archived, or otherwise
                # inaccessible project from a denied member.
                raise PermissionError(_ACCESS_DENIED)

        platforms = _fetch_all(
            conn,
            """
            select platform_key as key, display_name as name, enabled, provider_status, sort_order
            from platform_registry
            order by sort_order, platform_key
            """,
        )

        if scoped_project_id is None:
            source_options = _fetch_all(
                conn,
                """
                select distinct source_type as value
                from discovery_event d
                join source_video v on v.id=d.video_id
                where (%s='all' or v.platform=%s)
                order by source_type
                """,
                (platform, platform),
            )
        else:
            source_options = _fetch_all(
                conn,
                """
                select distinct inclusion_row.source_type as value
                from project_video_inclusion inclusion_row
                join source_video v on v.id=inclusion_row.video_id
                where inclusion_row.project_id=%s::uuid
                  and inclusion_row.status <> 'archived'
                  and (%s='all' or v.platform=%s)
                order by inclusion_row.source_type
                """,
                (scoped_project_id, platform, platform),
            )

        total_row = _fetch_one(
            conn,
            base_cte
            + f"""
            select count(*)::int as total
            from source_video v
            {scope_join}
            left join source_account a on a.id=v.account_id
            left join latest_metric m on m.video_id=v.id
            {total_private_joins}
            where {active_where_sql}
            """,
            scope_join_args + active_args,
        )
        total = int(total_row.get("total") or 0)
        offset = (page - 1) * page_size

        items = _fetch_all(
            conn,
            base_cte
            + f"""
            select
              v.id::text,
              v.platform,
              v.platform_video_id,
              coalesce(v.title, v.description, '(无标题)') as title,
              v.description,
              v.source_url,
              v.published_at,
              v.duration_ms,
              {item_research_fields}
              {item_account_id_field}
              {item_project_status_field}
              a.nickname as account_name,
              m.play_count,
              m.like_count,
              m.comment_count,
              m.share_count,
              m.collect_count,
              m.author_follower_count,
              m.captured_at as metric_captured_at,
              m.metric_source_kind,
              m.metric_provenance,
              {item_priority_field}
              case when coalesce(m.author_follower_count,0) > 0
                then round(
                  100.0 * (
                    coalesce(m.like_count,0)
                    + coalesce(m.comment_count,0)
                    + coalesce(m.share_count,0)
                  ) / m.author_follower_count, 2
                )
                else null
              end as follower_efficiency,
              {item_source_fields}
            from source_video v
            {scope_join}
            left join source_account a on a.id=v.account_id
            left join latest_metric m on m.video_id=v.id
            {item_private_joins}
            where {active_where_sql}
            order by {order_by}
            limit %s offset %s
            """,
            scope_join_args + active_args + (page_size, offset),
        )

        selected_id = selected_video_id or (items[0]["id"] if items else "")
        detail = {}
        if selected_id:
            scoped_sources: list[str] = []
            try:
                normalized_selected_id = UUID(selected_id)
            except (TypeError, ValueError, AttributeError):
                raise ValueError("selected_video_id must be a UUID") from None
            if scoped_project_id is not None:
                with conn.cursor(row_factory=dict_row) as cur:
                    allowed = _can_read_project_video(
                        cur,
                        project_id=scoped_project_id,
                        actor=actor,
                        video_id=normalized_selected_id,
                    )
                if not allowed:
                    # A known UUID from another project must not disclose whether it
                    # exists, has comments, or has private review artifacts.
                    raise PermissionError(_ACCESS_DENIED)
                scoped_inclusion = _fetch_one(
                    conn,
                    """
                    select source_type
                    from project_video_inclusion
                    where project_id=%s::uuid and video_id=%s::uuid
                      and status <> 'archived'
                    """,
                    (scoped_project_id, normalized_selected_id),
                )
                if not scoped_inclusion:
                    raise PermissionError(_ACCESS_DENIED)
                scoped_sources = [scoped_inclusion["source_type"]]
            detail = _fetch_one(
                conn,
                base_cte
                + f"""
                select
                  v.id::text,
                  v.platform,
                  v.platform_video_id,
                  coalesce(v.title, v.description, '(无标题)') as title,
                  v.description,
                  v.source_url,
                  v.published_at,
                  v.duration_ms,
                  {item_research_fields}
                  {item_project_status_field}
                  a.nickname as account_name,
                  m.play_count,
                  m.like_count,
                  m.comment_count,
                  m.share_count,
                  m.collect_count,
                  m.author_follower_count,
                  m.captured_at as metric_captured_at,
                  m.metric_source_kind,
                  m.metric_provenance,
                  {item_priority_field}
                  case when coalesce(m.author_follower_count,0) > 0
                    then round(
                      100.0 * (
                        coalesce(m.like_count,0)
                        + coalesce(m.comment_count,0)
                        + coalesce(m.share_count,0)
                      ) / m.author_follower_count, 2
                    )
                    else null
                  end as follower_efficiency,
                  {item_source_fields}
                from source_video v
                {scope_join}
                left join source_account a on a.id=v.account_id
                left join latest_metric m on m.video_id=v.id
                {item_private_joins}
                where v.id=%s::uuid
                """,
                scope_join_args + (selected_id,),
            )

            if scoped_project_id is None:
                evidence = _fetch_all(
                    conn,
                    """
                    select
                      source_type, source_key, discovered_at, rank_value
                    from discovery_event
                    where video_id=%s::uuid
                    order by discovered_at desc
                    limit 8
                    """,
                    (selected_id,),
                )
            else:
                evidence = []
                # Scores, collection membership and discovery history are
                # global research state.  A project view exposes none of them.
                for field in (
                    "research_level", "monitoring_status", "priority",
                    "sources", "source_count", "collection_count",
                ):
                    detail.pop(field, None)
                detail["research_level"] = None
                detail["monitoring_status"] = None
                detail["priority"] = None
                detail["sources"] = scoped_sources
                detail["source_count"] = len(scoped_sources)
                detail["collection_count"] = None

            comments = _fetch_all(
                conn,
                """
                select
                  platform_comment_id, text_content, like_count, published_at
                from video_comment
                where video_id=%s::uuid
                order by like_count desc nulls last, captured_at desc
                limit 5
                """,
                (selected_id,),
            )
            if scoped_project_id is None:
                asr_row = _legacy_analysis_row(
                    conn,
                    project_id=None,
                    sql=
                    """
                select
                  left(t.text_content, %s) as text,
                  char_length(t.text_content) > %s as truncated,
                  t.quality_status,
                  t.asr_provider,
                  t.model_id,
                  t.model_revision,
                  t.engine_version,
                  t.language,
                  t.audio_duration_ms,
                  t.created_at,
                  c.api_cost,
                  c.asr_cost,
                  c.llm_cost,
                  c.total_cost,
                  c.cost_currency,
                  c.cost_basis
                from transcript t
                join research_task_cost c on c.id=t.task_cost_id
                where t.video_id=%s::uuid
                  and c.status='completed'
                  and c.task_type='asr_transcription'
                  and c.video_id=t.video_id
                  and c.input_fingerprint=t.source_fingerprint
                order by t.created_at desc, t.id desc
                limit 1
                """,
                    args=(_ASR_TRANSCRIPT_TEXT_LIMIT, _ASR_TRANSCRIPT_TEXT_LIMIT, selected_id),
                )
                l3_row = _legacy_analysis_row(
                    conn,
                    project_id=None,
                    sql=
                    """
                select
                  a.analysis_type,
                  a.model,
                  a.model_revision,
                  a.prompt_version,
                  a.schema_version,
                  a.output,
                  a.created_at,
                  c.api_cost,
                  c.asr_cost,
                  c.llm_cost,
                  coalesce(c.total_cost, a.cost_amount) as total_cost,
                  coalesce(c.cost_currency, a.cost_currency) as cost_currency,
                  c.cost_basis
                from analysis_run a
                join research_task_cost c on c.id=a.task_cost_id
                where a.video_id=%s::uuid
                  and a.analysis_level='L3'
                  and a.analysis_type=%s
                  and a.status='completed'
                  and a.schema_version=%s
                  and a.output->>'privacy_reviewed'='true'
                  and c.status='completed'
                  and c.task_type=%s
                  and c.task_version=%s
                  and c.video_id=a.video_id
                  and c.input_fingerprint=a.input_fingerprint
                  and c.output_fingerprint=a.output_fingerprint
                order by a.created_at desc, a.id desc
                limit 1
                """,
                    args=(
                        selected_id,
                        _L3_ANALYSIS_TYPE,
                        _L3_SCHEMA_VERSION,
                        _L3_ANALYSIS_TYPE,
                        _L3_SCHEMA_VERSION,
                    ),
                )
            else:
                # Project results are read only from project-private execution
                # tables, after the project/video ACL check above.  A global
                # transcript or analysis_run is never a project fallback.
                asr_row = _fetch_one(
                    conn,
                    """
                    select t.id::text as transcript_id, left(t.text_content, %s) as text,
                      char_length(t.text_content) > %s as truncated,
                      'unreviewed' as quality_status,
                      t.asr_provider, t.model_id, t.model_revision,
                      t.engine_version, t.language, t.audio_duration_ms,
                      t.created_at, c.api_cost, c.asr_cost, c.llm_cost,
                      c.total_cost, c.cost_currency, c.cost_basis
                    from project_transcript t
                    join project_asr_execution_job j
                      on j.id=t.execution_job_id and j.project_id=t.project_id
                      and j.video_id=t.video_id and j.status='completed'
                    join project_asr_media_review r
                      on r.id=t.media_review_id and r.project_id=t.project_id
                      and r.video_id=t.video_id and r.status='approved'
                    left join project_research_task_cost c
                      on c.id=t.task_cost_id and c.project_id=t.project_id
                      and c.video_id=t.video_id and c.status='completed'
                      and c.task_type='asr_transcription' and c.task_key=j.task_key
                    where t.project_id=%s::uuid and t.video_id=%s::uuid
                      and exists (select 1 from project_video_inclusion current_inclusion
                        where current_inclusion.project_id=t.project_id
                          and current_inclusion.video_id=t.video_id
                          and current_inclusion.status='accepted')
                    order by t.created_at desc, t.id desc limit 1
                    """,
                    (_ASR_TRANSCRIPT_TEXT_LIMIT, _ASR_TRANSCRIPT_TEXT_LIMIT,
                     scoped_project_id, normalized_selected_id),
                )
                l3_row = _fetch_one(
                    conn,
                    """
                    select a.analysis_type, j.model_id as model,
                      j.model_revision, j.prompt_version, j.schema_version,
                      a.output, a.created_at, c.api_cost, c.asr_cost,
                      c.llm_cost, c.total_cost, c.cost_currency, c.cost_basis
                    from project_l3_analysis_result a
                    join project_l3_execution_job j
                      on j.id=a.execution_job_id and j.project_id=a.project_id
                      and j.video_id=a.video_id and j.status='completed'
                    join project_l3_privacy_review r
                      on r.id=a.privacy_review_id and r.project_id=a.project_id
                      and r.video_id=a.video_id and r.status='approved'
                    join project_transcript t
                      on t.id=r.transcript_id and t.project_id=a.project_id
                      and t.video_id=a.video_id
                    join project_asr_media_review media_review
                      on media_review.id=t.media_review_id
                      and media_review.project_id=a.project_id
                      and media_review.video_id=a.video_id
                      and media_review.status='approved'
                    left join project_research_task_cost c
                      on c.id=a.task_cost_id and c.project_id=a.project_id
                      and c.video_id=a.video_id and c.status='completed'
                      and c.task_type='l3_structured_research' and c.task_key=j.task_key
                    where a.project_id=%s::uuid and a.video_id=%s::uuid
                      and a.analysis_type=%s and j.schema_version=%s
                      and exists (select 1 from project_video_inclusion current_inclusion
                        where current_inclusion.project_id=a.project_id
                          and current_inclusion.video_id=a.video_id
                          and current_inclusion.status='accepted')
                    order by a.created_at desc, a.id desc limit 1
                    """,
                    (scoped_project_id, normalized_selected_id,
                     _L3_ANALYSIS_TYPE, _L3_SCHEMA_VERSION),
                )
            detail["evidence"] = evidence
            detail["comments"] = comments
            detail["asr_transcript"] = (
                _public_asr_transcript(asr_row) if asr_row else None
            )
            detail["l3_analysis"] = (
                {
                    "analysis_type": l3_row["analysis_type"],
                    "model": l3_row["model"],
                    "model_revision": l3_row["model_revision"],
                    "prompt_version": l3_row["prompt_version"],
                    "schema_version": l3_row["schema_version"],
                    "created_at": l3_row["created_at"],
                    "output": _public_l3_output(l3_row["output"]),
                    "cost": {
                        "api_cost": l3_row["api_cost"],
                        "asr_cost": l3_row["asr_cost"],
                        "llm_cost": l3_row["llm_cost"],
                        "total_cost": l3_row["total_cost"],
                        "currency": l3_row["cost_currency"],
                        "basis": l3_row["cost_basis"],
                    },
                }
                if l3_row
                else None
            )

    return {
        "platforms": platforms,
        "source_options": source_options,
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items,
        "detail": detail,
    }
