# py: ==3.14.*
"""Display-safe, paginated video metric history for the Case detail view."""

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


class postgresql(TypedDict):
    host: str
    port: int
    user: str
    password: str
    dbname: str
    sslmode: str


_ACTOR_RE = re.compile(r"^[^\s@]{1,128}@[^\s@]{1,120}$")


def _actor() -> str:
    actor = os.environ.get("WM_END_USER_EMAIL", "").strip().lower()
    if not _ACTOR_RE.fullmatch(actor) or len(actor) > 254:
        raise PermissionError("RESEARCH_VIDEO_ACCESS_DENIED")
    return actor


def _get_legacy_allowlist() -> str:
    import wmill

    return wmill.get_variable("f/content_research/research_action_writers")


def _legacy_allowed(actor: str, allowlist: str) -> bool:
    if not isinstance(allowlist, str) or not allowlist.strip():
        return False
    try:
        values = json.loads(allowlist) if allowlist.lstrip().startswith("[") else re.split(r"[,\n]", allowlist)
    except json.JSONDecodeError:
        return False
    return isinstance(values, list) and any(
        isinstance(value, str) and value.strip().lower() == actor for value in values
    )


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
    project_id: str | None = None,
):
    """Return only normalized metric columns; raw provider metrics never leave DB."""

    days = max(1, min(int(days or 30), 90))
    page = max(1, int(page or 1))
    page_size = min(100, max(10, int(page_size or 20)))
    offset = (page - 1) * page_size
    actor = _actor()
    try:
        scoped_project = UUID(project_id) if project_id else None
        normalized_video = UUID(video_id)
    except (TypeError, ValueError, AttributeError):
        raise ValueError("project_id or video_id is invalid") from None
    if scoped_project is None:
        try:
            allowed = _legacy_allowed(actor, _get_legacy_allowlist())
        except Exception:
            allowed = False
        if not allowed:
            raise PermissionError("RESEARCH_VIDEO_ACCESS_DENIED")
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
        if scoped_project is not None:
            cur.execute(
                "select project_video_can_read(%s, %s, %s) as allowed",
                (scoped_project, actor, normalized_video),
            )
            if not cur.fetchone()["allowed"]:
                raise PermissionError("RESEARCH_VIDEO_ACCESS_DENIED")
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
                when 'douyin.app.multi_video' then 'detail'
                when 'douyin.app.one_video' then 'detail'
                when 'douyin.app.video_statistics' then 'detail'
                when 'douyin.app.multi_video_statistics' then 'detail'
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
