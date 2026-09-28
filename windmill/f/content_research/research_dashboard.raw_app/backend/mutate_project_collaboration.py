# py: ==3.14.*
#requirements:
#psycopg[binary]==3.3.6

"""Manage project members and opt-in video-evidence sharing.

Only Windmill's authenticated end-user email is an actor. The database role is
not treated as the end-user identity, even if the resource happens to be owner.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
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
_ROLES = frozenset({"owner", "admin", "researcher", "analyst", "viewer"})
_ACTIONS = frozenset({"member_set", "member_revoke", "share_offer", "share_accept", "share_decline", "share_revoke"})


def _email(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("email is invalid")
    normalized = value.strip().lower()
    if len(normalized) > 254 or not _EMAIL.fullmatch(normalized):
        raise ValueError("email is invalid")
    return normalized


def _actor() -> str:
    raw = os.environ.get("WM_END_USER_EMAIL", "")
    try:
        return _email(raw)
    except ValueError:
        raise PermissionError("RESEARCH_ACTION_IDENTITY_REQUIRED") from None


def _uuid(value: object, field: str) -> UUID:
    if not isinstance(value, str):
        raise ValueError(f"{field} is invalid")
    try:
        return UUID(value)
    except ValueError:
        raise ValueError(f"{field} is invalid") from None


def _connect(db: postgresql):
    args: dict[str, Any] = {
        "host": db["host"], "port": int(db.get("port", 5432)),
        "user": db["user"], "password": db["password"],
        "dbname": db["dbname"], "sslmode": db.get("sslmode", "prefer"),
    }
    if db.get("options"):
        args["options"] = db["options"]
    return psycopg.connect(**args, row_factory=dict_row)


def _project(cur, project_id: UUID) -> dict[str, Any]:
    # Lock the project to serialize role changes and prevent concurrent removal
    # of the last owner. Lock source/target in UUID order for sharing changes.
    cur.execute(
        """select p.id, p.organization_id, p.status, o.status as organization_status
           from research_project p join research_organization o on o.id=p.organization_id
           where p.id=%s for update of p""",
        (project_id,),
    )
    row = cur.fetchone()
    if not row or row["status"] != "active" or row["organization_status"] != "active":
        raise PermissionError("RESEARCH_PROJECT_ACCESS_DENIED")
    return dict(row)


def _role(cur, project_id: UUID, actor: str) -> str | None:
    cur.execute(
        """select role, status, effective_until
           from research_project_member
           where project_id=%s and actor_id=%s and effective_from <= now()
           order by effective_from desc limit 1""",
        (project_id, actor),
    )
    row = cur.fetchone()
    if row and row["status"] == "active" and (row["effective_until"] is None or row["effective_until"] > datetime.now(timezone.utc)):
        return str(row["role"])
    return None


def _manager(role: str | None) -> None:
    if role not in {"owner", "admin"}:
        raise PermissionError("RESEARCH_PROJECT_MANAGE_DENIED")


def _event(cur, *, project_id: UUID, actor: str, action: str,
           subject: str | None = None, related: UUID | None = None,
           grant_id: UUID | None = None) -> None:
    cur.execute(
        """insert into project_access_event
           (project_id, actor_id, action, subject_actor_id, related_project_id, grant_id)
           values (%s,%s,%s,%s,%s,%s)""",
        (project_id, actor, action, subject, related, grant_id),
    )


def _member_set(cur, project_id: UUID, actor: str, manager_role: str,
                target_actor: object, role: object) -> dict[str, Any]:
    target = _email(target_actor)
    if role not in _ROLES:
        raise ValueError("role is invalid")
    old_role = _role(cur, project_id, target)
    if manager_role != "owner" and (role in {"owner", "admin"} or old_role in {"owner", "admin"}):
        raise PermissionError("RESEARCH_PROJECT_MANAGE_DENIED")
    if old_role == role:
        return {"changed": False, "member": target, "role": role}
    if old_role == "owner" and role != "owner":
        _require_other_owner(cur, project_id, target)
    cur.execute(
        """insert into research_project_member(project_id, actor_id, role, status)
           values (%s,%s,%s,'active')""",
        (project_id, target, role),
    )
    _event(cur, project_id=project_id, actor=actor,
           action="member_role_change" if old_role else "member_add", subject=target)
    return {"changed": True, "member": target, "role": role}


def _require_other_owner(cur, project_id: UUID, target: str) -> None:
    cur.execute(
        """select count(*) as n from research_project_member m
           where m.project_id=%s and m.actor_id<>%s
             and m.role='owner' and m.status='active'
             and m.effective_from <= now()
             and (m.effective_until is null or m.effective_until > now())
             and not exists (
               select 1 from research_project_member newer
               where newer.project_id=m.project_id and newer.actor_id=m.actor_id
                 and newer.effective_from <= now()
                 and newer.effective_from > m.effective_from)""",
        (project_id, target),
    )
    if cur.fetchone()["n"] == 0:
        raise ValueError("last project owner cannot be removed")


def _member_revoke(cur, project_id: UUID, actor: str, manager_role: str,
                   target_actor: object) -> dict[str, Any]:
    target = _email(target_actor)
    old_role = _role(cur, project_id, target)
    if old_role is None:
        return {"changed": False, "member": target}
    if manager_role != "owner" and old_role in {"owner", "admin"}:
        raise PermissionError("RESEARCH_PROJECT_MANAGE_DENIED")
    if old_role == "owner":
        _require_other_owner(cur, project_id, target)
    cur.execute(
        """insert into research_project_member(project_id, actor_id, role, status)
           values (%s,%s,%s,'revoked')""",
        (project_id, target, old_role),
    )
    _event(cur, project_id=project_id, actor=actor, action="member_revoke", subject=target)
    return {"changed": True, "member": target}


def _share_offer(cur, source_id: UUID, target_id: UUID, actor: str,
                 expires_days: object) -> dict[str, Any]:
    if type(expires_days) is not int or not 1 <= expires_days <= 365:
        raise ValueError("expires_days must be 1..365")
    cur.execute(
        """select id, status, effective_until from project_video_share_grant
           where source_project_id=%s and target_project_id=%s
             and scope='public_video_evidence' and status in ('offered','active')
           for update""",
        (source_id, target_id),
    )
    existing = cur.fetchone()
    if existing:
        if existing["effective_until"] > datetime.now(timezone.utc):
            raise ValueError("an open grant already exists")
        cur.execute(
            """update project_video_share_grant set status='revoked',
                 closed_by=%s, closed_at=now() where id=%s""",
            (actor, existing["id"]),
        )
        _event(cur, project_id=source_id, actor=actor, action="share_revoke",
               related=target_id, grant_id=existing["id"])
    cur.execute(
        """insert into project_video_share_grant
           (source_project_id,target_project_id,offered_by,effective_until)
           values (%s,%s,%s,%s) returning id""",
        (source_id, target_id, actor, datetime.now(timezone.utc) + timedelta(days=expires_days)),
    )
    grant_id = cur.fetchone()["id"]
    _event(cur, project_id=source_id, actor=actor, action="share_offer",
           related=target_id, grant_id=grant_id)
    return {"changed": True, "grant_id": str(grant_id), "status": "offered"}


def _share_transition(cur, grant_id: UUID, project_id: UUID, actor: str,
                      action: str) -> dict[str, Any]:
    cur.execute(
        """select source_project_id, target_project_id, status, effective_until
           from project_video_share_grant where id=%s for update""",
        (grant_id,),
    )
    row = cur.fetchone()
    if not row or project_id not in (row["source_project_id"], row["target_project_id"]):
        raise PermissionError("RESEARCH_PROJECT_ACCESS_DENIED")
    if action in {"share_accept", "share_decline"}:
        if project_id != row["target_project_id"] or row["status"] != "offered" or row["effective_until"] <= datetime.now(timezone.utc):
            raise PermissionError("RESEARCH_PROJECT_SHARE_DENIED")
        if action == "share_accept":
            cur.execute(
                """select 1 from research_project source_project
                   join research_project target_project
                     on target_project.id=%s
                    and target_project.organization_id=source_project.organization_id
                   join research_organization organization_row
                     on organization_row.id=source_project.organization_id
                   where source_project.id=%s
                     and source_project.status='active'
                     and target_project.status='active'
                     and organization_row.status='active'""",
                (row["target_project_id"], row["source_project_id"]),
            )
            if cur.fetchone() is None:
                raise PermissionError("RESEARCH_PROJECT_SHARE_DENIED")
        status = "active" if action == "share_accept" else "declined"
        cur.execute(
            """update project_video_share_grant set status=%s,
                 accepted_by=case when %s='active' then %s else accepted_by end,
                 accepted_at=case when %s='active' then now() else accepted_at end,
                 closed_by=case when %s='declined' then %s else closed_by end,
                 closed_at=case when %s='declined' then now() else closed_at end
               where id=%s""",
            (status, status, actor, status, status, actor, status, grant_id),
        )
    else:
        if row["status"] not in {"offered", "active"}:
            raise PermissionError("RESEARCH_PROJECT_SHARE_DENIED")
        cur.execute(
            """update project_video_share_grant
               set status='revoked', closed_by=%s, closed_at=now()
               where id=%s""",
            (actor, grant_id),
        )
        status = "revoked"
    _event(cur, project_id=project_id, actor=actor, action=action,
           related=row["target_project_id"] if project_id == row["source_project_id"] else row["source_project_id"],
           grant_id=grant_id)
    return {"changed": True, "grant_id": str(grant_id), "status": status}


def main(db: postgresql, project_id: str, action: str,
         member_email: str | None = None, role: str | None = None,
         target_project_id: str | None = None, grant_id: str | None = None,
         expires_days: int = 30):
    actor = _actor()
    if action not in _ACTIONS:
        raise ValueError("action is invalid")
    project = _uuid(project_id, "project_id")
    target = _uuid(target_project_id, "target_project_id") if action == "share_offer" else None
    grant = _uuid(grant_id, "grant_id") if action in {"share_accept", "share_decline", "share_revoke"} else None
    if target == project:
        raise ValueError("a project cannot share with itself")
    try:
        with _connect(db) as conn, conn.cursor() as cur:
            if target:
                locked = {pid: _project(cur, pid) for pid in sorted((project, target))}
                if locked[project]["organization_id"] != locked[target]["organization_id"]:
                    raise PermissionError("RESEARCH_PROJECT_SHARE_DENIED")
            else:
                _project(cur, project)
            manager_role = _role(cur, project, actor)
            _manager(manager_role)
            if action == "member_set":
                return _member_set(cur, project, actor, manager_role, member_email, role)
            if action == "member_revoke":
                return _member_revoke(cur, project, actor, manager_role, member_email)
            if action == "share_offer":
                return _share_offer(cur, project, target, actor, expires_days)
            return _share_transition(cur, grant, project, actor, action)
    except (PermissionError, ValueError):
        raise
    except psycopg.errors.UniqueViolation:
        raise ValueError("concurrent collaboration change; refresh and retry") from None
    except Exception:
        raise RuntimeError("RESEARCH_PROJECT_COLLABORATION_UNAVAILABLE") from None
