# py: ==3.14.*
"""Read bounded original records for one video inside the authenticated app."""

from __future__ import annotations

import json
import os
import re
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
_ACTOR_RE = re.compile(r"^[^\s@]{1,128}@[^\s@]{1,120}$")
_ACCESS_DENIED = "VIDEO_RAW_RECORDS_ACCESS_DENIED"


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


def _get_legacy_allowlist() -> str | None:
    """Read the server-side allowlist; never accept it from an app caller."""
    try:
        import wmill
        value = wmill.get_variable("f/content_research/research_action_writers")
    except Exception:
        return None
    return value if isinstance(value, str) else None


def _can_read_project_video(cur, *, project_id: UUID, actor: str, video_id: UUID) -> bool:
    # This database predicate owns all distinction between a
    # missing project/video, revoked member, archived inclusion and cross-project
    # video. Call it before querying canonical or raw provider records.
    cur.execute(
        "select project_video_can_read(%s::uuid, %s, %s::uuid) as allowed",
        (project_id, actor, video_id),
    )
    row = cur.fetchone()
    return bool(row and row["allowed"])


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


def main(
    db: postgresql,
    video_id: str,
    project_id: str | None = None,
):
    try:
        normalized_id = UUID(str(video_id))
    except (TypeError, ValueError):
        raise ValueError("video_id must be a UUID") from None

    normalized_project_id = _project_id(project_id)
    actor = _actor()
    connect_args = {
        "host": db["host"],
        "port": int(db.get("port", 5432)),
        "user": db["user"],
        "password": db["password"],
        "dbname": db["dbname"],
        "sslmode": db.get("sslmode", "prefer"),
    }
    with psycopg.connect(**connect_args) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("set transaction isolation level repeatable read read only")
        if normalized_project_id is None:
            if not _legacy_reader_allowed(actor, _get_legacy_allowlist()):
                raise PermissionError(_ACCESS_DENIED)
        elif not _can_read_project_video(
            cur, project_id=normalized_project_id, actor=actor, video_id=normalized_id,
        ):
            raise PermissionError(_ACCESS_DENIED)

        if normalized_project_id is None:
            cur.execute(
                """
                select v.id::text as video_id, v.platform, v.platform_video_id,
                       v.title, v.description, v.source_url, v.published_at,
                       v.duration_ms, v.availability_status, v.research_level,
                       v.monitoring_status, v.monitoring_priority,
                       v.first_seen_at, v.last_seen_at,
                       a.id::text as account_id, a.platform_account_id,
                       a.nickname as account_name
                from source_video v left join source_account a on a.id=v.account_id
                where v.id=%s
                """, (normalized_id,),
            )
        else:
            # Project ACL grants the public work, not global research operations.
            cur.execute(
                """
                select v.id::text as video_id, v.platform, v.platform_video_id,
                       v.title, v.description, v.source_url, v.published_at,
                       v.duration_ms, v.availability_status,
                       a.platform_account_id, a.nickname as account_name
                from source_video v left join source_account a on a.id=v.account_id
                where v.id=%s
                """, (normalized_id,),
            )
        canonical_row = cur.fetchone()
        if canonical_row is None:
            raise PermissionError(_ACCESS_DENIED)
        canonical = _json(dict(canonical_row))

        if normalized_project_id is None:
            provider_snapshots = _rows(cur, """
                select id, provider, provider_object_id, captured_at, raw_payload
                from provider_video_snapshot where video_id=%s
                order by captured_at desc, id desc limit 10
            """, (normalized_id,))
            for row in provider_snapshots:
                row["raw_payload"] = _raw(row.get("raw_payload"))
            metric_snapshots = _rows(cur, """
                select id, provider, source_endpoint, observation_key, captured_at,
                       play_count, like_count, comment_count, share_count,
                       collect_count, author_follower_count, raw_metrics
                from metric_snapshot where video_id=%s
                order by captured_at desc, id desc limit 50
            """, (normalized_id,))
            for row in metric_snapshots:
                row["raw_metrics"] = _raw(row.get("raw_metrics"))
            discoveries = _rows(cur, """
                select id, provider, source_type, source_key, observation_key,
                       discovered_at, rank_value, rule_version, metadata
                from discovery_event where video_id=%s
                order by discovered_at desc, id desc limit 50
            """, (normalized_id,))
            for row in discoveries:
                row["metadata"] = _raw(row.get("metadata"))
            comments = _rows(cur, """
                select id::text, provider, source_endpoint, platform_comment_id,
                       parent_platform_comment_id, text_content, like_count, reply_count,
                       sample_reason, published_at, captured_at, last_seen_at,
                       observation_count, raw_payload
                from video_comment where video_id=%s
                order by captured_at desc, id desc limit 50
            """, (normalized_id,))
            for row in comments:
                row["raw_payload"] = _raw(row.get("raw_payload"))
            return {
                "video_id": str(normalized_id), "canonical": canonical,
                "provider_snapshots": provider_snapshots, "metric_snapshots": metric_snapshots,
                "discoveries": discoveries, "comments": comments,
                "limits": {"provider_snapshots": 10, "metric_snapshots": 50,
                           "discoveries": 50, "comments": 50,
                           "raw_json_chars_per_field": _RAW_JSON_LIMIT},
                "read_only": True,
            }

        cur.execute("""
            select source_type, status, first_seen_at, last_seen_at
            from project_video_inclusion where project_id=%s and video_id=%s
        """, (normalized_project_id, normalized_id))
        inclusion = cur.fetchone()
        if inclusion is None:
            raise PermissionError(_ACCESS_DENIED)
        cur.execute("""
            select play_count, like_count, comment_count, share_count, collect_count,
                   author_follower_count, captured_at, oldest_field_captured_at,
                   metric_source_kind, metric_provenance
            from merged_video_metric where video_id=%s
        """, (normalized_id,))
        merged_metric = cur.fetchone()
        comments = _rows(cur, """
            select platform_comment_id, parent_platform_comment_id, text_content,
                   like_count, reply_count, published_at
            from video_comment where video_id=%s
            order by published_at desc nulls last, id desc limit 50
        """, (normalized_id,))

    return {
        "video_id": str(normalized_id), "canonical": canonical,
        "project_inclusion": _json(dict(inclusion)),
        "merged_metrics": _json(dict(merged_metric)) if merged_metric is not None else None,
        "comments": comments,
        "limits": {"comments": 50}, "read_only": True,
    }
