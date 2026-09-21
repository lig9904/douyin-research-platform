"""Display-safe, paginated video metric history for the Case detail view."""

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


def main(
    db: postgresql,
    video_id: str,
    days: int = 30,
    page: int = 1,
    page_size: int = 20,
):
    """Return only normalized metric columns; raw provider metrics never leave DB."""

    days = max(1, min(int(days or 30), 90))
    page = max(1, int(page or 1))
    page_size = min(100, max(10, int(page_size or 20)))
    offset = (page - 1) * page_size
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
        cur.execute(
            """
            select count(*)::int as total
            from metric_snapshot
            where video_id=%s::uuid
              and captured_at >= now() - (%s || ' days')::interval
            """,
            (video_id, days),
        )
        total = int(cur.fetchone()["total"])
        cur.execute(
            """
            select
              captured_at,
              case source_endpoint
                when 'douyin.billboard.low_fan' then 'billboard'
                when 'douyin.app.multi_video_v2' then 'detail'
                else 'other'
              end as source_kind,
              play_count,
              like_count,
              comment_count,
              share_count,
              collect_count,
              author_follower_count
            from metric_snapshot
            where video_id=%s::uuid
              and captured_at >= now() - (%s || ' days')::interval
            order by captured_at desc, id desc
            limit %s offset %s
            """,
            (video_id, days, page_size, offset),
        )
        items = [_json(dict(row)) for row in cur.fetchall()]
    return {
        "video_id": video_id,
        "days": days,
        "page": page,
        "page_size": page_size,
        "total": total,
        "items": items,
        "excluded_fields": ["provider", "source_endpoint", "observation_key", "raw_metrics"],
    }
