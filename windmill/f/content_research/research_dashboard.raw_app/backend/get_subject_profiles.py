# py: ==3.14.*
#requirements:
#psycopg[binary]==3.3.6

"""Read actor-scoped, approved subject profiles without leaking draft sources."""

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


_EMAIL = re.compile(r"^[^\s@]{1,128}@[^\s@]{1,120}$")
_MANAGERS = frozenset({"owner", "admin"})


def _actor() -> str:
    actor = os.environ.get("WM_END_USER_EMAIL", "").strip().lower()
    if len(actor) > 254 or not _EMAIL.fullmatch(actor):
        raise PermissionError("RESEARCH_ACTION_IDENTITY_REQUIRED")
    return actor


def _uuid(value: object, field: str) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise ValueError(f"{field.upper()}_INVALID") from None


def _connect(db: postgresql):
    args: dict[str, Any] = {
        "host": db["host"], "port": int(db.get("port", 5432)),
        "user": db["user"], "password": db["password"],
        "dbname": db["dbname"], "sslmode": db.get("sslmode", "prefer"),
    }
    if db.get("options"):
        args["options"] = db["options"]
    return psycopg.connect(**args, row_factory=dict_row)


def _json(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: str(value) if isinstance(value, UUID) else value.isoformat() if isinstance(value, datetime) else value
        for key, value in row.items()
    }


def main(
    db: postgresql, project_id: str, subject_id: str = "", include_history: bool = False,
):
    actor = _actor()
    project = _uuid(project_id, "project_id")
    subject = None if not subject_id else _uuid(subject_id, "subject_id")
    if not isinstance(include_history, bool):
        raise ValueError("INCLUDE_HISTORY_INVALID")
    try:
        with _connect(db) as conn, conn.cursor() as cur:
            cur.execute("set transaction isolation level repeatable read read only")
            cur.execute(
                """select member.role
                   from research_project project
                   join research_organization organization on organization.id=project.organization_id
                   join lateral (
                     select role, status, effective_until from research_project_member
                     where project_id=project.id and actor_id=%s and effective_from<=now()
                     order by effective_from desc limit 1
                   ) member on true
                   where project.id=%s and project.status='active' and organization.status='active'
                     and member.status='active'
                     and (member.effective_until is null or member.effective_until>now())
                     and project_actor_can_read(project.id,%s)""",
                (actor, project, actor),
            )
            member = cur.fetchone()
            if member is None:
                raise PermissionError("RESEARCH_PROJECT_ACCESS_DENIED")
            can_manage = str(member["role"]) in _MANAGERS
            if subject is not None:
                cur.execute(
                    "select 1 from research_subject where id=%s and project_id=%s",
                    (subject, project),
                )
                if cur.fetchone() is None:
                    raise PermissionError("RESEARCH_SUBJECT_ACCESS_DENIED")
            detailed = can_manage and include_history
            if detailed:
                columns = """id, project_id, subject_id, profile_kind, version_no, status, summary,
                              rights_status, source_reference, source_digest, content_fingerprint,
                              approved_by, approved_at, superseded_at, revoked_at, created_at, updated_at"""
                predicate = "project_id=%s" + (" and subject_id=%s" if subject is not None else "")
            else:
                columns = """id, project_id, subject_id, profile_kind, version_no, 'approved'::text as status, summary,
                              rights_status, content_fingerprint, approved_at"""
                predicate = "project_id=%s and status='approved'" + (" and subject_id=%s" if subject is not None else "")
            cur.execute(
                f"""select {columns} from research_subject_profile_version
                    where {predicate}
                    order by subject_id, profile_kind, version_no desc, id""",
                (project,) if subject is None else (project, subject),
            )
            profiles = [_json(dict(row)) for row in cur.fetchall()]
        return {"project_id": str(project), "profiles": profiles, "can_manage": can_manage}
    except (PermissionError, ValueError):
        raise
    except Exception:
        raise RuntimeError("SUBJECT_PROFILES_UNAVAILABLE") from None
