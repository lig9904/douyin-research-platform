# py: ==3.14.*
"""Read bounded original records for one video inside the authenticated app."""

from __future__ import annotations

import json
from datetime import date, datetime
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


_SENSITIVE_KEY_PARTS = (
    "api_key",
    "access_key",
    "secret",
    "authorization",
    "cookie",
    "password",
    "token",
)
_RAW_JSON_LIMIT = 100_000


def _json(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    return value


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]"
            if any(part in str(key).lower() for part in _SENSITIVE_KEY_PARTS)
            else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return _json(value)


def _raw(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    text = json.dumps(_redact(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "json_text": text[:_RAW_JSON_LIMIT],
        "truncated": len(text) > _RAW_JSON_LIMIT,
        "stored_chars": len(text),
    }


def _rows(cur, sql: str, args: tuple[Any, ...]) -> list[dict[str, Any]]:
    cur.execute(sql, args)
    return [_json(dict(row)) for row in cur.fetchall()]


def main(db: postgresql, video_id: str):
    try:
        normalized_id = UUID(str(video_id))
    except (TypeError, ValueError):
        raise ValueError("video_id must be a UUID") from None

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
            select v.id::text as video_id, v.platform, v.platform_video_id,
                   v.title, v.description, v.source_url, v.published_at,
                   v.duration_ms, v.availability_status, v.research_level,
                   v.monitoring_status, v.monitoring_priority,
                   v.first_seen_at, v.last_seen_at,
                   a.id::text as account_id, a.platform_account_id,
                   a.nickname as account_name
            from source_video v
            left join source_account a on a.id=v.account_id
            where v.id=%s
            """,
            (normalized_id,),
        )
        canonical_row = cur.fetchone()
        if canonical_row is None:
            raise ValueError("video does not exist")
        canonical = _json(dict(canonical_row))

        provider_snapshots = _rows(
            cur,
            """
            select id, provider, provider_object_id, captured_at, raw_payload
            from provider_video_snapshot
            where video_id=%s
            order by captured_at desc, id desc
            limit 10
            """,
            (normalized_id,),
        )
        for row in provider_snapshots:
            row["raw_payload"] = _raw(row.get("raw_payload"))

        metric_snapshots = _rows(
            cur,
            """
            select id, provider, source_endpoint, observation_key, captured_at,
                   play_count, like_count, comment_count, share_count,
                   collect_count, author_follower_count, raw_metrics
            from metric_snapshot
            where video_id=%s
            order by captured_at desc, id desc
            limit 50
            """,
            (normalized_id,),
        )
        for row in metric_snapshots:
            row["raw_metrics"] = _raw(row.get("raw_metrics"))

        discoveries = _rows(
            cur,
            """
            select id, provider, source_type, source_key, observation_key,
                   discovered_at, rank_value, rule_version, metadata
            from discovery_event
            where video_id=%s
            order by discovered_at desc, id desc
            limit 50
            """,
            (normalized_id,),
        )
        for row in discoveries:
            row["metadata"] = _raw(row.get("metadata"))

        comments = _rows(
            cur,
            """
            select id::text, provider, source_endpoint, platform_comment_id,
                   parent_platform_comment_id, text_content, like_count, reply_count,
                   sample_reason, published_at, captured_at, last_seen_at,
                   observation_count, raw_payload
            from video_comment
            where video_id=%s
            order by captured_at desc, id desc
            limit 50
            """,
            (normalized_id,),
        )
        for row in comments:
            row["raw_payload"] = _raw(row.get("raw_payload"))

    return {
        "video_id": str(normalized_id),
        "canonical": canonical,
        "provider_snapshots": provider_snapshots,
        "metric_snapshots": metric_snapshots,
        "discoveries": discoveries,
        "comments": comments,
        "limits": {
            "provider_snapshots": 10,
            "metric_snapshots": 50,
            "discoveries": 50,
            "comments": 50,
            "raw_json_chars_per_field": _RAW_JSON_LIMIT,
        },
        "read_only": True,
    }
