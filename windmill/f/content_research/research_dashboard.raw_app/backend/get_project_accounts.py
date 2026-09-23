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


def main(db: postgresql, project_id: str, limit: int = _MAX_LIMIT):
    """Return at most 100 public account records after a fail-closed ACL check."""
    actor = _actor()
    project_uuid = _project_uuid(project_id)
    safe_limit = _safe_limit(limit)
    try:
        with _connect(db) as conn, conn.cursor() as cur:
            cur.execute("set transaction read only")
            cur.execute(
                """
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
                """,
                (project_uuid, actor),
            )
            project = cur.fetchone()
            # Do not reveal whether a denied id belongs to another organization,
            # a different member, or no project at all.
            if project is None:
                raise PermissionError("PROJECT_ACCESS_DENIED")
            cur.execute(
                """
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
                  a.last_seen_at
                from project_account_relation r
                join source_account a on a.id = r.source_account_id
                left join research_subject s
                  on s.id = r.subject_id and s.project_id = r.project_id
                where r.project_id = %s
                  and r.verification_status = 'verified'
                  and r.effective_from <= now()
                  and (r.effective_until is null or r.effective_until > now())
                  and (s.id is null or s.status = 'active')
                order by a.platform, a.nickname nulls last, a.platform_account_id, r.id
                limit %s
                """,
                (project_uuid, safe_limit),
            )
            accounts = [_json(dict(row)) for row in cur.fetchall()]
        return {"project": _json(dict(project)), "accounts": accounts, "read_only": True}
    except (PermissionError, ValueError):
        raise
    except Exception:
        raise RuntimeError("PROJECT_ACCOUNTS_UNAVAILABLE") from None
