# py: ==3.14.*
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, TypedDict
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from f.content_research.research_tool_lib.account_similarity import (
    AccountSimilarityProfile,
    rank_similar_accounts,
)


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
    content_domain: str = "all",
    follower_min: int = -1,
    follower_max: int = -1,
    account_type: str = "all",
    location: str = "all",
    certification_type: str = "all",
    posts_min: int = -1,
    posts_max: int = -1,
    follower_growth_min: int = -1,
    follower_growth_max: int = -1,
    monitoring_status: str = "all",
    query: str = "",
    page: int = 1,
    page_size: int = 10,
    sort: str = "followers_desc",
    selected_account_id: str = "",
):
    platform = (platform or "all").strip()
    days = max(1, min(int(days or 30), 365))
    research_level = int(research_level)
    follower_min = int(follower_min)
    follower_max = int(follower_max)
    posts_min = int(posts_min)
    posts_max = int(posts_max)
    follower_growth_min = int(follower_growth_min)
    follower_growth_max = int(follower_growth_max)
    page = max(1, int(page or 1))
    page_size = min(50, max(10, int(page_size or 10)))
    query = (query or "").strip()

    sort_map = {
        "followers_desc": "follower_count desc nulls last, a.last_seen_at desc",
        "growth_desc": "follower_growth desc nulls last, follower_count desc nulls last",
        "posts_desc": "posts_count desc, follower_count desc nulls last",
        "likes_desc": "avg_like_count desc nulls last, follower_count desc nulls last",
        "blackhorse_desc": "blackhorse_count desc, follower_count desc nulls last",
        "recent_desc": "a.last_seen_at desc",
    }
    order_by = sort_map.get(sort, sort_map["followers_desc"])

    base_cte = """
    with latest_account_metric as (
      select distinct on (account_id)
        account_id, follower_count, following_count, total_favorited,
        video_count, captured_at
      from account_metric_snapshot
      order by account_id, captured_at desc, id desc
    ),
    first_window_metric as (
      select distinct on (account_id)
        account_id, follower_count, captured_at
      from account_metric_snapshot
      where captured_at >= now() - (%s || ' days')::interval
      order by account_id, captured_at asc, id asc
    ),
    metric_window_count as (
      select account_id, count(*)::int as snapshot_count
      from account_metric_snapshot
      where captured_at >= now() - (%s || ' days')::interval
      group by account_id
    ),
    latest_video_metric as (
      select
        video_id, play_count, like_count, comment_count, share_count, captured_at
      from merged_video_metric
    ),
    latest_video_score as (
      select distinct on (video_id)
        video_id, score, calculated_at
      from video_score
      where score_type='priority'
      order by video_id, calculated_at desc, id desc
    ),
    period_video_stats as (
      select
        v.account_id,
        count(*)::int as posts_count,
        avg(m.like_count)::numeric as avg_like_count,
        avg(m.comment_count)::numeric as avg_comment_count,
        avg(m.share_count)::numeric as avg_share_count,
        sum(coalesce(m.play_count,0))::bigint as play_total,
        sum(coalesce(m.like_count,0))::bigint as like_total,
        sum(coalesce(m.comment_count,0))::bigint as comment_total,
        count(*) filter (where coalesce(s.score,0) >= 60)::int as blackhorse_count
      from source_video v
      left join latest_video_metric m on m.video_id=v.id
      left join latest_video_score s on s.video_id=v.id
      where v.account_id is not null
        and v.published_at >= now() - (%s || ' days')::interval
      group by v.account_id
    ),
    account_domains as (
      select account_id,
             array_agg(distinct tag_value order by tag_value)
               filter (where tag_type='content_domain') as content_domains
      from account_tag
      group by account_id
    ),
    account_rows as (
      select
        a.id,
        a.platform,
        a.platform_account_id,
        a.nickname,
        a.profile_url,
        a.bio,
        a.location_text,
        a.account_type,
        a.certification_type,
        a.research_level,
        a.monitoring_status,
        a.monitoring_priority,
        a.last_seen_at,
        m.follower_count,
        m.following_count,
        m.total_favorited,
        m.video_count,
        m.captured_at as metric_captured_at,
        coalesce(vs.posts_count,0) as posts_count,
        vs.avg_like_count,
        vs.avg_comment_count,
        vs.avg_share_count,
        coalesce(vs.play_total,0) as play_total,
        coalesce(vs.like_total,0) as like_total,
        coalesce(vs.comment_total,0) as comment_total,
        coalesce(vs.blackhorse_count,0) as blackhorse_count,
        case
          when coalesce(mc.snapshot_count,0) >= 2
           and m.follower_count is not null
           and fm.follower_count is not null
          then m.follower_count - fm.follower_count
          else null
        end as follower_growth,
        coalesce(d.content_domains, array[]::text[]) as content_domains
      from source_account a
      left join latest_account_metric m on m.account_id=a.id
      left join first_window_metric fm on fm.account_id=a.id
      left join metric_window_count mc on mc.account_id=a.id
      left join period_video_stats vs on vs.account_id=a.id
      left join account_domains d on d.account_id=a.id
    )
    """

    where_sql = """
      (%s='all' or a.platform=%s)
      and (%s < 0 or a.research_level=%s)
      and (%s='all' or %s = any(a.content_domains))
      and (%s < 0 or a.follower_count >= %s)
      and (%s < 0 or a.follower_count <= %s)
      and (%s='all' or coalesce(a.account_type,'')=%s)
      and (%s='all' or coalesce(a.location_text,'')=%s)
      and (%s='all' or coalesce(a.certification_type,'')=%s)
      and (%s < 0 or a.posts_count >= %s)
      and (%s < 0 or a.posts_count <= %s)
      and (%s < 0 or a.follower_growth >= %s)
      and (%s < 0 or a.follower_growth <= %s)
      and (%s='all' or a.monitoring_status=%s)
      and (
        %s=''
        or coalesce(a.nickname,'') ilike '%%' || %s || '%%'
        or coalesce(a.platform_account_id,'') ilike '%%' || %s || '%%'
        or coalesce(a.bio,'') ilike '%%' || %s || '%%'
      )
    """

    args = (
        platform, platform,
        research_level, research_level,
        content_domain, content_domain,
        follower_min, follower_min,
        follower_max, follower_max,
        account_type, account_type,
        location, location,
        certification_type, certification_type,
        posts_min, posts_min,
        posts_max, posts_max,
        follower_growth_min, follower_growth_min,
        follower_growth_max, follower_growth_max,
        monitoring_status, monitoring_status,
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

        domain_options = _fetch_all(
            conn,
            """
            select distinct t.tag_value as value
            from account_tag t
            join source_account a on a.id=t.account_id
            where t.tag_type='content_domain'
              and (%s='all' or a.platform=%s)
            order by t.tag_value
            """,
            (platform, platform),
        )

        account_type_options = _fetch_all(
            conn,
            """
            select distinct account_type as value
            from source_account
            where account_type is not null and btrim(account_type)<>''
              and (%s='all' or platform=%s)
            order by account_type
            """,
            (platform, platform),
        )

        location_options = _fetch_all(
            conn,
            """
            select distinct location_text as value
            from source_account
            where location_text is not null and btrim(location_text)<>''
              and (%s='all' or platform=%s)
            order by location_text
            """,
            (platform, platform),
        )

        certification_options = _fetch_all(
            conn,
            """
            select distinct certification_type as value
            from source_account
            where certification_type is not null and btrim(certification_type)<>''
              and (%s='all' or platform=%s)
            order by certification_type
            """,
            (platform, platform),
        )

        total_row = _fetch_one(
            conn,
            base_cte
            + f"""
            select count(*)::int as total
            from account_rows a
            where {where_sql}
            """,
            (days, days, days) + args,
        )
        total = int(total_row.get("total") or 0)
        offset = (page - 1) * page_size

        items = _fetch_all(
            conn,
            base_cte
            + f"""
            select
              a.id::text,
              a.platform,
              a.platform_account_id,
              a.nickname,
              a.profile_url,
              a.bio,
              a.location_text,
              a.account_type,
              a.certification_type,
              a.research_level,
              a.monitoring_status,
              a.monitoring_priority,
              a.last_seen_at,
              a.follower_count,
              a.following_count,
              a.total_favorited,
              a.video_count,
              a.metric_captured_at,
              a.posts_count,
              a.avg_like_count,
              a.avg_comment_count,
              a.avg_share_count,
              a.play_total,
              a.like_total,
              a.comment_total,
              a.blackhorse_count,
              a.follower_growth,
              a.content_domains
            from account_rows a
            where {where_sql}
            order by {order_by}
            limit %s offset %s
            """,
            (days, days, days) + args + (page_size, offset),
        )

        selected_id = selected_account_id or (items[0]["id"] if items else "")
        detail: dict[str, Any] = {}

        if selected_id:
            try:
                selected_id = str(UUID(selected_id))
            except (TypeError, ValueError, AttributeError):
                return {
                    "platforms": platforms,
                    "domain_options": domain_options,
                    "account_type_options": account_type_options,
                    "location_options": location_options,
                    "certification_options": certification_options,
                    "total": total,
                    "page": page,
                    "page_size": page_size,
                    "items": items,
                    "detail": {},
                }

            detail = _fetch_one(
                conn,
                base_cte
                + """
                select
                  a.id::text,
                  a.platform,
                  a.platform_account_id,
                  a.nickname,
                  a.profile_url,
                  a.bio,
                  a.location_text,
                  a.account_type,
                  a.certification_type,
                  a.research_level,
                  a.monitoring_status,
                  a.monitoring_priority,
                  a.last_seen_at,
                  a.follower_count,
                  a.following_count,
                  a.total_favorited,
                  a.video_count,
                  a.metric_captured_at,
                  a.posts_count,
                  a.avg_like_count,
                  a.avg_comment_count,
                  a.avg_share_count,
                  a.play_total,
                  a.like_total,
                  a.comment_total,
                  a.blackhorse_count,
                  a.follower_growth,
                  a.content_domains
                from account_rows a
                where a.id=%s::uuid
                """,
                (days, days, days, selected_id),
            )

            if not detail:
                return {
                    "platforms": platforms,
                    "domain_options": domain_options,
                    "account_type_options": account_type_options,
                    "location_options": location_options,
                    "certification_options": certification_options,
                    "total": total,
                    "page": page,
                    "page_size": page_size,
                    "items": items,
                    "detail": {},
                }

            trend = _fetch_all(
                conn,
                """
                select
                  captured_at,
                  follower_count,
                  following_count,
                  total_favorited,
                  video_count
                from account_metric_snapshot
                where account_id=%s::uuid
                  and captured_at >= now() - (%s || ' days')::interval
                order by captured_at asc, id asc
                limit 120
                """,
                (selected_id, days),
            )

            hot_videos = _fetch_all(
                conn,
                """
                with latest_metric as (
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
                  v.platform_video_id,
                  coalesce(v.title,v.description,'(无标题)') as title,
                  v.source_url,
                  v.published_at,
                  v.duration_ms,
                  m.play_count,
                  m.like_count,
                  m.comment_count,
                  m.share_count,
                  coalesce(s.score,v.monitoring_priority,0)::numeric as priority,
                  v.research_level
                from source_video v
                left join latest_metric m on m.video_id=v.id
                left join latest_score s on s.video_id=v.id
                where v.account_id=%s::uuid
                order by coalesce(s.score,v.monitoring_priority,0) desc,
                         m.like_count desc nulls last,
                         v.published_at desc nulls last
                limit 5
                """,
                (selected_id,),
            )

            detail["trend"] = trend
            detail["hot_videos"] = hot_videos
            detail["fan_profile_available"] = False
            similarity_rows = _fetch_all(
                conn,
                base_cte
                + """
                select
                  a.id::text,
                  a.platform,
                  a.nickname,
                  a.content_domains,
                  a.account_type,
                  a.certification_type,
                  a.follower_count,
                  a.video_count
                from account_rows a
                where a.platform=%s
                  and a.id<>%s::uuid
                order by a.id
                """,
                (days, days, days, detail["platform"], selected_id),
            )
            target_profile = _similarity_profile(detail)
            candidate_profiles = [_similarity_profile(row) for row in similarity_rows]
            matches = rank_similar_accounts(target_profile, candidate_profiles)
            by_id = {row["id"]: row for row in similarity_rows}
            detail["similar_accounts"] = [
                {
                    "id": match.account_id,
                    "nickname": by_id[match.account_id]["nickname"],
                    "similarity_score": match.similarity_score,
                    "evidence_coverage": match.evidence_coverage,
                    "raw_score": match.raw_score,
                    "matched_domains": list(match.matched_domains),
                    "matched_fields": list(match.matched_fields),
                    "components": match.components,
                }
                for match in matches
            ]
            detail["similar_accounts_available"] = bool(matches)

    return {
        "platforms": platforms,
        "domain_options": domain_options,
        "account_type_options": account_type_options,
        "location_options": location_options,
        "certification_options": certification_options,
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items,
        "detail": detail,
    }


def _similarity_profile(row: dict[str, Any]) -> AccountSimilarityProfile:
    return AccountSimilarityProfile(
        account_id=row["id"],
        platform=row["platform"],
        content_domains=frozenset(row.get("content_domains") or ()),
        account_type=row.get("account_type"),
        certification_type=row.get("certification_type"),
        follower_count=row.get("follower_count"),
        video_count=row.get("video_count"),
    )
