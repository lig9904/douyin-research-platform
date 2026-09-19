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

    sort_map = {
        "published_desc": "v.published_at desc nulls last, v.last_seen_at desc",
        "priority_desc": "priority desc, v.last_seen_at desc",
        "likes_desc": "m.like_count desc nulls last, v.last_seen_at desc",
        "plays_desc": "m.play_count desc nulls last, v.last_seen_at desc",
    }
    order_by = sort_map.get(sort, sort_map["published_desc"])

    where_sql = """
      (%s='all' or v.platform=%s)
      and v.last_seen_at >= now() - (%s || ' days')::interval
      and (%s < 0 or v.research_level=%s)
      and (%s < 0 or coalesce(s.score, v.monitoring_priority, 0) >= %s)
      and (%s='all' or v.monitoring_status=%s)
      and (%s < 0 or coalesce(m.play_count,0) >= %s)
      and (%s < 0 or coalesce(m.play_count,0) <= %s)
      and (%s < 0 or coalesce(m.author_follower_count,0) >= %s)
      and (%s < 0 or coalesce(m.author_follower_count,0) <= %s)
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
        platform, platform, days,
        research_level, research_level,
        priority_min, priority_min,
        status, status,
        play_min, play_min,
        play_max, play_max,
        follower_min, follower_min,
        follower_max, follower_max,
        collected, collected, collected,
        query, query, query, query,
        source_type, source_type,
    )

    base_cte = """
    with latest_metric as (
      select distinct on (video_id)
        video_id, play_count, like_count, comment_count, share_count,
        collect_count, author_follower_count, captured_at
      from metric_snapshot
      order by video_id, captured_at desc, id desc
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

    with _connect(db) as conn:
        platforms = _fetch_all(
            conn,
            """
            select platform_key as key, display_name as name, enabled, provider_status, sort_order
            from platform_registry
            order by sort_order, platform_key
            """,
        )

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

        total_row = _fetch_one(
            conn,
            base_cte
            + f"""
            select count(*)::int as total
            from source_video v
            left join source_account a on a.id=v.account_id
            left join latest_metric m on m.video_id=v.id
            left join latest_score s on s.video_id=v.id
            left join collections c on c.video_id=v.id
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
              v.id::text,
              v.platform,
              v.platform_video_id,
              coalesce(v.title, v.description, '(无标题)') as title,
              v.description,
              v.source_url,
              v.published_at,
              v.duration_ms,
              v.research_level,
              v.monitoring_status,
              v.monitoring_priority,
              a.id::text as account_id,
              a.nickname as account_name,
              m.play_count,
              m.like_count,
              m.comment_count,
              m.share_count,
              m.collect_count,
              m.author_follower_count,
              m.captured_at as metric_captured_at,
              coalesce(s.score, v.monitoring_priority, 0)::numeric as priority,
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
              coalesce(h.sources, array[]::text[]) as sources,
              coalesce(h.source_count,0) as source_count,
              coalesce(c.collection_count,0) as collection_count
            from source_video v
            left join source_account a on a.id=v.account_id
            left join latest_metric m on m.video_id=v.id
            left join latest_score s on s.video_id=v.id
            left join source_hits h on h.video_id=v.id
            left join collections c on c.video_id=v.id
            where {where_sql}
            order by {order_by}
            limit %s offset %s
            """,
            args + (page_size, offset),
        )

        selected_id = selected_video_id or (items[0]["id"] if items else "")
        detail = {}
        if selected_id:
            detail = _fetch_one(
                conn,
                base_cte
                + """
                select
                  v.id::text,
                  v.platform,
                  v.platform_video_id,
                  coalesce(v.title, v.description, '(无标题)') as title,
                  v.description,
                  v.source_url,
                  v.published_at,
                  v.duration_ms,
                  v.research_level,
                  v.monitoring_status,
                  a.nickname as account_name,
                  m.play_count,
                  m.like_count,
                  m.comment_count,
                  m.share_count,
                  m.collect_count,
                  m.author_follower_count,
                  m.captured_at as metric_captured_at,
                  coalesce(s.score, v.monitoring_priority, 0)::numeric as priority,
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
                  coalesce(h.sources, array[]::text[]) as sources,
                  coalesce(h.source_count,0) as source_count,
                  coalesce(c.collection_count,0) as collection_count
                from source_video v
                left join source_account a on a.id=v.account_id
                left join latest_metric m on m.video_id=v.id
                left join latest_score s on s.video_id=v.id
                left join source_hits h on h.video_id=v.id
                left join collections c on c.video_id=v.id
                where v.id=%s::uuid
                """,
                (selected_id,),
            )

            evidence = _fetch_all(
                conn,
                """
                select
                  source_type, source_key, discovered_at, rank_value, metadata
                from discovery_event
                where video_id=%s::uuid
                order by discovered_at desc
                limit 8
                """,
                (selected_id,),
            )

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
            detail["evidence"] = evidence
            detail["comments"] = comments

    return {
        "platforms": platforms,
        "source_options": source_options,
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items,
        "detail": detail,
    }
