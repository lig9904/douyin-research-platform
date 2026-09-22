# py: ==3.14.*
"""Read-only, privacy-bounded global search for the research dashboard."""

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


def _normalize_query(query: str) -> str:
    if not isinstance(query, str):
        return ""
    return " ".join(query.split())[:200]


def main(
    db: postgresql,
    query: str = "",
    platform: str = "all",
    page: int = 1,
    page_size: int = 20,
):
    """Search display-safe video, account, and hotspot metadata only.

    Transcript text, human annotations, raw payloads, request identifiers, and
    provider responses are deliberately outside this endpoint's search scope.
    """

    query = _normalize_query(query)
    platform = platform.strip() if isinstance(platform, str) else "all"
    page = max(1, int(page or 1))
    page_size = min(50, max(10, int(page_size or 20)))
    offset = (page - 1) * page_size
    if not query:
        return {
            "query": "",
            "platform": platform,
            "page": page,
            "page_size": page_size,
            "total": 0,
            "items": [],
            "searched_fields": ["video_title", "video_description", "account_nickname", "signal_title", "signal_description"],
            "excluded_fields": ["transcript_text", "human_annotation", "raw_provider_payload"],
        }

    pattern = f"%{query}%"
    video_platform_filter = "(%s='all' or v.platform=%s)"
    signal_platform_filter = "(%s='all' or s.platform=%s)"
    union_sql = f"""
      select
        'video'::text as kind,
        v.id::text as id,
        coalesce(v.title, v.description, '(无标题视频)') as title,
        coalesce(a.nickname, '未知账号') as subtitle,
        v.description as excerpt,
        v.platform,
        v.published_at as observed_at,
        v.research_level,
        null::text as signal_type
      from source_video v
      left join source_account a on a.id=v.account_id
      where {video_platform_filter}
        and (
          coalesce(v.title, '') ilike %s
          or coalesce(v.description, '') ilike %s
          or coalesce(a.nickname, '') ilike %s
        )

      union all

      select
        'signal'::text as kind,
        s.id::text as id,
        coalesce(s.title, s.description, '(无标题信号)') as title,
        s.signal_type as subtitle,
        s.description as excerpt,
        s.platform,
        s.last_seen_at as observed_at,
        s.research_level,
        s.signal_type
      from external_signal s
      where {signal_platform_filter}
        and (
          coalesce(s.title, '') ilike %s
          or coalesce(s.description, '') ilike %s
        )
    """
    args = (
        platform,
        platform,
        pattern,
        pattern,
        pattern,
        platform,
        platform,
        pattern,
        pattern,
    )
    connect_args = {
        "host": db["host"],
        "port": int(db.get("port", 5432)),
        "user": db["user"],
        "password": db["password"],
        "dbname": db["dbname"],
        "sslmode": db.get("sslmode", "prefer"),
    }
    with psycopg.connect(**connect_args) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("set transaction read only")
        cur.execute(f"select count(*)::int as total from ({union_sql}) matches", args)
        total = int(cur.fetchone()["total"])
        cur.execute(
            f"""
            select * from ({union_sql}) matches
            order by observed_at desc nulls last, kind, title
            limit %s offset %s
            """,
            args + (page_size, offset),
        )
        items = [_json(dict(row)) for row in cur.fetchall()]
    return {
        "query": query,
        "platform": platform,
        "page": page,
        "page_size": page_size,
        "total": total,
        "items": items,
        "searched_fields": ["video_title", "video_description", "account_nickname", "signal_title", "signal_description"],
        "excluded_fields": ["transcript_text", "human_annotation", "raw_provider_payload"],
    }
