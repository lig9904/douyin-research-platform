# py: ==3.14.*
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from decimal import Decimal
from typing import Any, TypedDict

import psycopg
from psycopg.rows import dict_row


_ACTOR_RE = re.compile(r"^[^\s@]{1,128}@[^\s@]{1,120}$")
_ACCESS_DENIED = "RESEARCH_LEGACY_CATALOG_ACCESS_DENIED"
_LEGACY_ADMIN_ALLOWLIST_PATH = "f/content_research/research_action_writers"


def _actor() -> str:
    """Return the Windmill-authenticated actor, never a caller-supplied value."""
    value = os.environ.get("WM_END_USER_EMAIL", "").strip().lower()
    if not _ACTOR_RE.fullmatch(value) or len(value) > 254:
        raise PermissionError(_ACCESS_DENIED)
    return value


def _legacy_admin_allowed(actor: str) -> bool:
    """Fail closed unless the server-owned legacy-admin list contains actor."""
    try:
        import wmill

        raw = wmill.get_variable(_LEGACY_ADMIN_ALLOWLIST_PATH)
    except Exception:
        return False
    if not isinstance(raw, str) or not raw.strip():
        return False
    try:
        values = json.loads(raw) if raw.lstrip().startswith("[") else re.split(r"[,\n]", raw)
    except json.JSONDecodeError:
        return False
    if not isinstance(values, list):
        return False
    return any(
        isinstance(value, str)
        and _ACTOR_RE.fullmatch(value.strip().lower())
        and value.strip().lower() == actor
        for value in values
    )


def _require_legacy_admin() -> None:
    actor = _actor()
    if not _legacy_admin_allowed(actor):
        raise PermissionError(_ACCESS_DENIED)


class postgresql(TypedDict):
    host: str
    port: int
    user: str
    password: str
    dbname: str
    sslmode: str


def _json(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value) if value.is_finite() else None
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


def _connect(db: postgresql):
    return psycopg.connect(
        host=db["host"],
        port=int(db.get("port", 5432)),
        user=db["user"],
        password=db["password"],
        dbname=db["dbname"],
        sslmode=db.get("sslmode", "prefer"),
    )


def main(
    db: postgresql,
    platform: str = "douyin",
    days: int = 7,
    signal_type: str = "all",
    category: str = "all",
    research_level: int = -1,
    monitoring_status: str = "all",
    heat_min: float = -1,
    query: str = "",
    page: int = 1,
    page_size: int = 10,
    sort: str = "heat_desc",
    selected_signal_id: str = "",
):
    # This endpoint is the old global hotspot catalogue.  Do not read any
    # global records until server identity and server-owned legacy admin policy
    # have both been verified.
    _require_legacy_admin()
    platform = (platform or "all").strip()
    days = max(1, min(int(days or 7), 365))
    research_level = int(research_level)
    heat_min = float(heat_min)
    page = max(1, int(page or 1))
    page_size = min(50, max(10, int(page_size or 10)))
    query = (query or "").strip()

    sort_map = {
        "heat_desc": "heat_value desc nulls last, s.last_seen_at desc",
        "growth_desc": "heat_growth_pct desc nulls last, heat_value desc nulls last",
        "discussion_desc": "discussion_count desc nulls last, heat_value desc nulls last",
        "related_desc": "related_video_count desc, heat_value desc nulls last",
        "recent_desc": "s.last_seen_at desc",
        "rank_asc": "rank_value asc nulls last, heat_value desc nulls last",
    }
    # A unique final key keeps tied rows from moving between pages.
    order_by = sort_map.get(sort, sort_map["heat_desc"]) + ", s.id asc"

    base_cte = """
    with latest_snapshot as (
      select distinct on (signal_id)
        id, signal_id, captured_at, rank_value, rank_change,
        heat_value, value_json
      from signal_snapshot
      where captured_at <= now()
      order by signal_id, captured_at desc, id desc
    ),
    previous_snapshot as (
      select distinct on (ss.signal_id)
        ss.signal_id, ss.captured_at, ss.rank_value, ss.heat_value
      from signal_snapshot ss
      join latest_snapshot ls on ls.signal_id=ss.signal_id
      -- Multiple ingestions at one observation time are not elapsed-time growth.
      -- Highest id wins within a timestamp, consistently with latest/trend.
      where ss.captured_at < ls.captured_at
      order by ss.signal_id, ss.captured_at desc, ss.id desc
    ),
    related_counts as (
      select signal_id, count(distinct video_id)::int as related_video_count
      from signal_video_link
      group by signal_id
    ),
    collections as (
      select signal_id, count(distinct collection_id)::int as collection_count
      from collection_item
      where signal_id is not null
      group by signal_id
    ),
    signal_rows as (
      select
        s.id,
        s.provider,
        s.platform,
        s.signal_type,
        s.provider_signal_id,
        s.signal_key,
        s.title,
        s.description,
        s.category_key,
        s.city_code,
        s.research_level,
        s.monitoring_status,
        s.monitoring_priority,
        s.first_seen_at,
        s.last_seen_at,
        ls.captured_at as snapshot_captured_at,
        ls.rank_value,
        ls.rank_change,
        ls.heat_value,
        case
          when coalesce(ls.value_json->>'discussion_count','') ~ '^[0-9]+(\\.[0-9]+)?$'
          then (ls.value_json->>'discussion_count')::numeric
          else null
        end as discussion_count,
        coalesce(rc.related_video_count,0) as related_video_count,
        coalesce(c.collection_count,0) as collection_count,
        case
          when ps.heat_value is not null
           and ps.heat_value > 0
           and ls.heat_value is not null
          then round(100.0 * (ls.heat_value - ps.heat_value) / ps.heat_value, 2)
          else null
        end as heat_growth_pct
      from external_signal s
      left join latest_snapshot ls on ls.signal_id=s.id
      left join previous_snapshot ps on ps.signal_id=s.id
      left join related_counts rc on rc.signal_id=s.id
      left join collections c on c.signal_id=s.id
    )
    """

    where_sql = """
      (%s='all' or s.platform=%s)
      and s.last_seen_at >= now() - (%s || ' days')::interval
      and (%s='all' or s.signal_type=%s)
      and (%s='all' or coalesce(s.category_key,'')=%s)
      and (%s < 0 or s.research_level=%s)
      and (%s='all' or s.monitoring_status=%s)
      and (%s < 0 or s.heat_value >= %s)
      and (
        %s=''
        or coalesce(s.title,'') ilike '%%' || %s || '%%'
        or coalesce(s.signal_key,'') ilike '%%' || %s || '%%'
        or coalesce(s.description,'') ilike '%%' || %s || '%%'
      )
    """

    args = (
        platform, platform,
        days,
        signal_type, signal_type,
        category, category,
        research_level, research_level,
        monitoring_status, monitoring_status,
        heat_min, heat_min,
        query, query, query, query,
    )

    with _connect(db) as conn:
        platforms = _fetch_all(
            conn,
            """
            select platform_key as key, display_name as name, enabled, provider_status, sort_order
            from platform_registry
            order by sort_order, platform_key
            """,
        )

        signal_type_options = _fetch_all(
            conn,
            """
            select distinct signal_type as value
            from external_signal
            where (%s='all' or platform=%s)
            order by signal_type
            """,
            (platform, platform),
        )

        category_options = _fetch_all(
            conn,
            """
            select distinct category_key as value
            from external_signal
            where category_key is not null and btrim(category_key)<>''
              and (%s='all' or platform=%s)
            order by category_key
            """,
            (platform, platform),
        )

        total_row = _fetch_one(
            conn,
            base_cte
            + f"""
            select count(*)::int as total
            from signal_rows s
            where {where_sql}
            """,
            args,
        )
        total = int(total_row.get("total") or 0)
        offset = (page - 1) * page_size

        items = _fetch_all(
            conn,
            base_cte
            + f"""
            select
              s.id::text,
              s.provider,
              s.platform,
              s.signal_type,
              s.provider_signal_id,
              s.signal_key,
              s.title,
              s.description,
              s.category_key,
              s.city_code,
              s.research_level,
              s.monitoring_status,
              s.monitoring_priority,
              s.first_seen_at,
              s.last_seen_at,
              s.snapshot_captured_at,
              s.rank_value,
              s.rank_change,
              s.heat_value,
              s.discussion_count,
              s.related_video_count,
              s.collection_count,
              s.heat_growth_pct
            from signal_rows s
            where {where_sql}
            order by {order_by}
            limit %s offset %s
            """,
            args + (page_size, offset),
        )

        # Detail belongs to the visible, filtered page. A stale or malformed id
        # must not bypass the platform/filter boundary or reach a UUID cast.
        visible_ids = {item["id"] for item in items}
        selected_id = (
            selected_signal_id
            if selected_signal_id in visible_ids
            else (items[0]["id"] if items else "")
        )
        detail: dict[str, Any] = {}

        if selected_id:
            detail = _fetch_one(
                conn,
                base_cte
                + """
                select
                  s.id::text,
                  s.provider,
                  s.platform,
                  s.signal_type,
                  s.provider_signal_id,
                  s.signal_key,
                  s.title,
                  s.description,
                  s.category_key,
                  s.city_code,
                  s.research_level,
                  s.monitoring_status,
                  s.monitoring_priority,
                  s.first_seen_at,
                  s.last_seen_at,
                  s.snapshot_captured_at,
                  s.rank_value,
                  s.rank_change,
                  s.heat_value,
                  s.discussion_count,
                  s.related_video_count,
                  s.collection_count,
                  s.heat_growth_pct
                from signal_rows s
                where s.id=%s::uuid
                """,
                (selected_id,),
            )

            trend = _fetch_all(
                conn,
                """
                with recent_points as (
                  select distinct on (captured_at)
                    id, captured_at, rank_value, rank_change, heat_value, value_json
                  from signal_snapshot
                  where signal_id=%s::uuid
                    and captured_at >= now() - (%s || ' days')::interval
                    and captured_at <= now()
                  order by captured_at desc, id desc
                  limit 240
                )
                select captured_at, rank_value, rank_change, heat_value, value_json
                from recent_points
                order by captured_at asc, id asc
                """,
                (selected_id, days),
            )

            related_videos = _fetch_all(
                conn,
                """
                with distinct_links as (
                  select distinct on (video_id)
                    video_id, relation_type, observed_at
                  from signal_video_link
                  where signal_id=%s::uuid
                  order by video_id, observed_at desc, relation_type asc
                ),
                latest_metric as (
                  select
                    video_id, play_count, like_count, comment_count, share_count, captured_at
                  from merged_video_metric
                ),
                latest_score as (
                  select distinct on (video_id)
                    video_id, score, calculated_at
                  from video_score
                  where score_type='priority'
                  order by video_id, calculated_at desc, id desc
                )
                select
                  v.id::text,
                  v.platform,
                  v.platform_video_id,
                  coalesce(v.title,v.description,'(无标题)') as title,
                  v.source_url,
                  v.published_at,
                  v.duration_ms,
                  v.research_level,
                  a.nickname as account_name,
                  m.play_count,
                  m.like_count,
                  m.comment_count,
                  m.share_count,
                  coalesce(sc.score,v.monitoring_priority,0)::numeric as priority,
                  l.relation_type,
                  l.observed_at
                from distinct_links l
                join source_video v on v.id=l.video_id
                left join source_account a on a.id=v.account_id
                left join latest_metric m on m.video_id=v.id
                left join latest_score sc on sc.video_id=v.id
                order by coalesce(sc.score,v.monitoring_priority,0) desc,
                         m.like_count desc nulls last,
                         l.observed_at desc,
                         v.id asc
                limit 8
                """,
                (selected_id,),
            )

            detail["trend"] = trend
            detail["related_videos"] = related_videos

    return {
        "platforms": platforms,
        "signal_type_options": signal_type_options,
        "category_options": category_options,
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items,
        "detail": detail,
    }
