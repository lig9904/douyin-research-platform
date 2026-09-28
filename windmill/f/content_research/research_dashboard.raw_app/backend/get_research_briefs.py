# py: ==3.14.*
#requirements:
#psycopg[binary]==3.3.6

"""Return the current actor's research briefs and safe run summaries."""

from __future__ import annotations

import os
import re
from datetime import datetime
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
    value = os.environ.get("WM_END_USER_EMAIL", "").strip().lower()
    if not _ACTOR_RE.fullmatch(value) or len(value) > 254:
        raise PermissionError("RESEARCH_ACTION_IDENTITY_REQUIRED")
    return value


def _project_id(value: object) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("project_id is invalid")
    try:
        return str(UUID(value))
    except ValueError:
        raise ValueError("project_id is invalid") from None


def _project_readable(cur, *, project_id: str, actor: str) -> None:
    cur.execute(
        """
        select 1
        from research_project project
        join research_organization organization on organization.id=project.organization_id
        join lateral (
          select status, effective_until
          from research_project_member
          where project_id=project.id and actor_id=%s and effective_from <= now()
          order by effective_from desc
          limit 1
        ) membership on true
        where project.id=%s and project.status='active' and organization.status='active'
          and membership.status='active'
          and (membership.effective_until is null or membership.effective_until > now())
        """,
        (actor, project_id),
    )
    if cur.fetchone() is None:
        # Same denial for a missing, inactive, or inaccessible project.
        raise PermissionError("RESEARCH_PROJECT_ACCESS_DENIED")


def _json(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json(item) for item in value]
    return value


def main(db: postgresql, project_id: str | None = None):
    actor = _actor()
    normalized_project_id = _project_id(project_id)
    connect_args = {
        "host": db["host"],
        "port": int(db.get("port", 5432)),
        "user": db["user"],
        "password": db["password"],
        "dbname": db["dbname"],
        "sslmode": db.get("sslmode", "prefer"),
    }
    try:
        with psycopg.connect(**connect_args, row_factory=dict_row) as conn, conn.cursor() as cur:
            # Keep authorization and the following brief/run reads on one
            # snapshot: a membership revocation or project archival cannot
            # otherwise race between the ACL check and the response.
            cur.execute("set transaction isolation level repeatable read read only")
            if normalized_project_id is not None:
                _project_readable(cur, project_id=normalized_project_id, actor=actor)
            cur.execute(
                """
                select id, subject_id, subject_gate_status, name, platform, source_type, target,
                  time_window_hours, max_items, depth, cadence_hours,
                  status, config_version, next_due_at, last_dispatched_at,
                  created_at, updated_at
                from research_brief
                where status <> 'archived'
                  and (
                    (project_id is null and %s::uuid is null and owner_actor=%s)
                    or (project_id=%s::uuid and %s::uuid is not null)
                  )
                order by created_at desc, id
                limit 100
                """,
                (normalized_project_id, actor, normalized_project_id, normalized_project_id),
            )
            briefs = [_json(dict(row)) for row in cur.fetchall()]
            brief_ids = [row["id"] for row in briefs]
            runs: list[dict[str, Any]] = []
            if brief_ids:
                cur.execute(
                    """
                    select r.id, r.brief_id, r.brief_version, r.trigger_kind,
                      r.status, r.source_run_id, r.summary, r.error_code,
                      r.started_at, r.finished_at
                    from research_brief_run r
                    join research_brief b on b.id=r.brief_id
                    where r.brief_id=any(%s::uuid[])
                    order by r.started_at desc, r.id
                    limit 200
                    """,
                    (brief_ids,),
                )
                runs = [_json(dict(row)) for row in cur.fetchall()]
        return {"briefs": briefs, "runs": runs, "read_only": True}
    except (PermissionError, ValueError):
        raise
    except Exception:
        raise RuntimeError("RESEARCH_BRIEFS_UNAVAILABLE") from None
