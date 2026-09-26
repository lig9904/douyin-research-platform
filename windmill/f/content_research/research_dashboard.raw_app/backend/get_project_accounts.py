# py: ==3.14.*
#requirements:
#psycopg[binary]==3.3.6

"""Return project-scoped public account relations for the current end user."""

from __future__ import annotations

import os
import re
from datetime import date, datetime
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
_MAX_LIMIT = 100


def _actor() -> str:
    value = os.environ.get("WM_END_USER_EMAIL", "").strip().lower()
    if not _ACTOR_RE.fullmatch(value) or len(value) > 254:
        raise PermissionError("RESEARCH_ACTION_IDENTITY_REQUIRED")
    return value


def _project_uuid(project_id: str) -> UUID:
    try:
        return UUID(str(project_id))
    except (TypeError, ValueError, AttributeError):
        raise ValueError("PROJECT_ID_INVALID") from None


def _safe_limit(value: int) -> int:
    try:
        return max(1, min(int(value), _MAX_LIMIT))
    except (TypeError, ValueError):
        return _MAX_LIMIT


def _safe_cursor(value: str | None) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise ValueError("PROJECT_ACCOUNT_CURSOR_INVALID") from None


def _json(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json(item) for item in value]
    return value


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
    return psycopg.connect(**args, row_factory=dict_row)


def main(db: postgresql, project_id: str, limit: int = _MAX_LIMIT, after_relation_id: str | None = None):
    """Return at most 100 public account records after a fail-closed ACL check."""
    actor = _actor()
    project_uuid = _project_uuid(project_id)
    safe_limit = _safe_limit(limit)
    cursor_id = _safe_cursor(after_relation_id)
    try:
        with _connect(db) as conn, conn.cursor() as cur:
            cur.execute("set transaction read only")
            cur.execute(
                """
                with authorized_project as materialized (
                  select p.id, p.slug, p.name, o.id as organization_id,
                    o.slug as organization_slug, o.name as organization_name
                  from research_project p
                  join research_organization o on o.id = p.organization_id
                  where p.id = %s
                    and p.status = 'active'
                    and o.status = 'active'
                    and exists (
                      select 1 from (
                        select m.status, m.effective_until
                        from research_project_member m
                        where m.project_id = p.id
                          and m.actor_id = %s
                          and m.effective_from <= now()
                        order by m.effective_from desc
                        limit 1
                      ) latest_member
                      where latest_member.status = 'active'
                        and (latest_member.effective_until is null or latest_member.effective_until > now())
                    )
                )
                select p.id as project_id, p.slug as project_slug,
                  p.name as project_name, p.organization_id,
                  p.organization_slug, p.organization_name,
                  account_page.*
                from authorized_project p
                left join lateral (
                  select
                    r.id as relation_id, r.relation_type, r.task_roles,
                    r.verification_status,
                    r.effective_from as relation_effective_from,
                    r.effective_until as relation_effective_until,
                    s.id as subject_id, s.name as subject_name,
                    s.subject_type, s.status as subject_status,
                    a.id as account_id, a.platform, a.platform_account_id,
                    a.nickname, a.profile_url, a.bio, a.location_text,
                    a.account_type, a.certification_type, a.first_seen_at,
                    a.last_seen_at,
                    coalesce(project_videos.included_public_video_count, 0)
                      as included_public_video_count,
                    project_videos.last_included_at,
                    latest_account_metric.follower_count,
                    latest_account_metric.captured_at as follower_captured_at
                  from project_account_relation r
                  join source_account a on a.id = r.source_account_id
                  left join research_subject s
                    on s.id = r.subject_id and s.project_id = r.project_id
                  left join lateral (
                    select
                      count(*)::int as included_public_video_count,
                      max(inclusion_row.last_seen_at) as last_included_at
                    from project_video_inclusion inclusion_row
                    join source_video video_row on video_row.id = inclusion_row.video_id
                    where inclusion_row.project_id = r.project_id
                      and inclusion_row.status <> 'archived'
                      and video_row.account_id = a.id
                  ) project_videos on true
                  left join lateral (
                    select metric_row.follower_count, metric_row.captured_at
                    from account_metric_snapshot metric_row
                    where metric_row.account_id = a.id
                      and metric_row.follower_count is not null
                    order by
                      coalesce(metric_row.observation_key like 'account-profile:%%'
                       and metric_row.captured_at >= now() - interval '30 days', false) desc,
                      metric_row.captured_at desc, metric_row.id desc
                    limit 1
                  ) latest_account_metric on true
                  where r.project_id = p.id
                    and r.id > coalesce(%s::uuid, '00000000-0000-0000-0000-000000000000'::uuid)
                    and r.verification_status = 'verified'
                    and r.effective_from <= now()
                    and (r.effective_until is null or r.effective_until > now())
                    and (s.id is null or s.status = 'active')
                  order by r.id
                  limit %s
                ) account_page on true
                order by account_page.relation_id
                """,
                (project_uuid, actor, cursor_id, safe_limit + 1),
            )
            rows = cur.fetchall()
            # A missing row covers absent, inactive and unauthorized projects alike.
            if not rows:
                raise PermissionError("PROJECT_ACCESS_DENIED")
            first = rows[0]
            project = {
                "id": first["project_id"],
                "slug": first["project_slug"],
                "name": first["project_name"],
                "organization_id": first["organization_id"],
                "organization_slug": first["organization_slug"],
                "organization_name": first["organization_name"],
            }
            project_columns = {
                "project_id", "project_slug", "project_name", "organization_id",
                "organization_slug", "organization_name",
            }
            accounts = [
                _json({key: value for key, value in row.items() if key not in project_columns})
                for row in rows if row["account_id"] is not None
            ]
        has_more = len(accounts) > safe_limit
        return {
            "project": _json(dict(project)),
            "accounts": accounts[:safe_limit],
            "has_more": has_more,
            "next_cursor": accounts[safe_limit - 1]["relation_id"] if has_more else None,
            "read_only": True,
        }
    except (PermissionError, ValueError):
        raise
    except Exception:
        raise RuntimeError("PROJECT_ACCOUNTS_UNAVAILABLE") from None
