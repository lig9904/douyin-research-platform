# py: ==3.14.*
#requirements:
#psycopg[binary]==3.3.6

"""Owner/admin-only, idempotent subject-profile lifecycle mutations."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from typing import Any, TypedDict
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


class postgresql(TypedDict):
    host: str
    port: int
    user: str
    password: str
    dbname: str
    sslmode: str


_EMAIL = re.compile(r"^[^\s@]{1,128}@[^\s@]{1,120}$")
_ACTIONS = frozenset({"create_draft", "approve", "supersede", "revoke"})
_KINDS = frozenset({"ip_narrative", "destination_experience", "activity_conversion", "other"})
_RIGHTS = frozenset({"unknown", "pending", "cleared", "restricted", "prohibited"})
_SUMMARY_KEYS = frozenset({
    "target_audience", "shootable_scenes", "narrative_constraints", "forbidden_expressions", "current_facts",
})


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


def _text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field.upper()}_INVALID")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum or "\x00" in normalized:
        raise ValueError(f"{field.upper()}_INVALID")
    return normalized


def _summary(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or not value or len(value) > 5 or set(value) - _SUMMARY_KEYS:
        raise ValueError("SUMMARY_INVALID")
    normalized: dict[str, list[str]] = {}
    for key, items in value.items():
        if not isinstance(items, list) or not items or len(items) > 50:
            raise ValueError("SUMMARY_INVALID")
        values: list[str] = []
        for item in items:
            if not isinstance(item, str):
                raise ValueError("SUMMARY_INVALID")
            text = " ".join(item.strip().split())
            if not text or len(text) > 1000 or "\x00" in text:
                raise ValueError("SUMMARY_INVALID")
            values.append(text)
        normalized[key] = values
    try:
        encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        raise ValueError("SUMMARY_INVALID") from None
    if len(encoded.encode()) > 8192:
        raise ValueError("SUMMARY_INVALID")
    return normalized


def _connect(db: postgresql):
    args: dict[str, Any] = {
        "host": db["host"], "port": int(db.get("port", 5432)),
        "user": db["user"], "password": db["password"],
        "dbname": db["dbname"], "sslmode": db.get("sslmode", "prefer"),
    }
    if db.get("options"):
        args["options"] = db["options"]
    return psycopg.connect(**args, row_factory=dict_row)


def _manager(cur, project: UUID, actor: str) -> None:
    cur.execute(
        """select 1 from research_project project
           join research_organization organization on organization.id=project.organization_id
           join lateral (
             select role, status, effective_until from research_project_member
             where project_id=project.id and actor_id=%s and effective_from<=now()
             order by effective_from desc limit 1
           ) member on true
           where project.id=%s and project.status='active' and organization.status='active'
             and member.status='active' and member.role in ('owner','admin')
             and (member.effective_until is null or member.effective_until>now())
           for update of project""",
        (actor, project),
    )
    if cur.fetchone() is None:
        raise PermissionError("RESEARCH_PROJECT_PROFILE_MANAGE_DENIED")


def _claim(cur, *, key: UUID, actor: str, action: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    action_type = f"subject_profile.{action}"
    cur.execute(
        """insert into research_user_action(idempotency_key,actor,action_type,payload_hash)
           values (%s,%s,%s,%s) on conflict do nothing returning idempotency_key""",
        (key, actor, action_type, digest),
    )
    if cur.fetchone() is not None:
        return None
    cur.execute("select actor, action_type, payload_hash, outcome from research_user_action where idempotency_key=%s", (key,))
    prior = cur.fetchone()
    if not prior or prior["actor"] != actor or prior["action_type"] != action_type or prior["payload_hash"] != digest or not isinstance(prior["outcome"], dict):
        raise ValueError("RESEARCH_ACTION_IDEMPOTENCY_CONFLICT")
    return {**prior["outcome"], "idempotent_replay": True, "db_writes": 0}


def _finish(cur, key: UUID, outcome: dict[str, Any]) -> dict[str, Any]:
    cur.execute("update research_user_action set outcome=%s where idempotency_key=%s", (Jsonb(outcome), key))
    return outcome


def _profile(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: str(value) if isinstance(value, UUID) else value.isoformat() if isinstance(value, datetime) else value
        for key, value in row.items()
    }


def _lock_subject(cur, project: UUID, subject: UUID) -> None:
    cur.execute(
        "select 1 from research_subject where id=%s and project_id=%s and status='active' for update",
        (subject, project),
    )
    if cur.fetchone() is None:
        raise PermissionError("RESEARCH_SUBJECT_ACCESS_DENIED")


def _lock_profile(cur, project: UUID, subject: UUID, profile: UUID) -> dict[str, Any]:
    _lock_subject(cur, project, subject)
    cur.execute(
        """select id, project_id, subject_id, profile_kind, version_no, status, summary, rights_status,
                  source_reference, source_digest, content_fingerprint, approved_by, approved_at,
                  superseded_at, revoked_at, created_at, updated_at
           from research_subject_profile_version
           where id=%s and project_id=%s and subject_id=%s for update""",
        (profile, project, subject),
    )
    row = cur.fetchone()
    if row is None:
        raise PermissionError("RESEARCH_SUBJECT_PROFILE_ACCESS_DENIED")
    return dict(row)


def main(
    db: postgresql, project_id: str, action: str, idempotency_key: str,
    subject_id: str, profile_id: str = "", profile_kind: str = "",
    version_no: int | None = None, summary: dict[str, Any] | None = None,
    rights_status: str = "", source_reference: str = "", source_digest: str = "",
):
    if action not in _ACTIONS:
        raise ValueError("ACTION_INVALID")
    actor = _actor()
    project = _uuid(project_id, "project_id")
    subject = _uuid(subject_id, "subject_id")
    key = _uuid(idempotency_key, "idempotency_key")
    profile = None if not profile_id else _uuid(profile_id, "profile_id")
    payload: dict[str, Any] = {"project_id": str(project), "subject_id": str(subject), "action": action}
    if action == "create_draft":
        if profile_kind not in _KINDS:
            raise ValueError("PROFILE_KIND_INVALID")
        if version_no is not None:
            raise ValueError("VERSION_NO_MANAGED")
        details = {
            "profile_kind": profile_kind, "summary": _summary(summary),
            "rights_status": rights_status, "source_reference": _text(source_reference, "source_reference", 512),
            "source_digest": _text(source_digest, "source_digest", 64).lower(),
        }
        if details["rights_status"] not in _RIGHTS:
            raise ValueError("RIGHTS_STATUS_INVALID")
        if not re.fullmatch(r"[0-9a-f]{64}", details["source_digest"]):
            raise ValueError("SOURCE_DIGEST_INVALID")
        payload.update(details)
    else:
        if profile is None:
            raise ValueError("PROFILE_ID_INVALID")
        payload["profile_id"] = str(profile)
    try:
        with _connect(db) as conn, conn.cursor() as cur:
            _manager(cur, project, actor)
            replay = _claim(cur, key=key, actor=actor, action=action, payload=payload)
            if replay is not None:
                return replay
            if action == "create_draft":
                _lock_subject(cur, project, subject)
                cur.execute(
                    """select coalesce(max(version_no), 0) + 1 as version_no
                       from research_subject_profile_version
                       where project_id=%s and subject_id=%s and profile_kind=%s""",
                    (project, subject, details["profile_kind"]),
                )
                next_version = int(cur.fetchone()["version_no"])
                cur.execute(
                    """insert into research_subject_profile_version(
                         project_id,subject_id,profile_kind,version_no,summary,rights_status,source_reference,source_digest
                       ) values (%s,%s,%s,%s,%s,%s,%s,%s)
                       returning id,project_id,subject_id,profile_kind,version_no,status,summary,rights_status,
                                 source_reference,source_digest,content_fingerprint,approved_by,approved_at,
                                 superseded_at,revoked_at,created_at,updated_at""",
                    (project, subject, details["profile_kind"], next_version, Jsonb(details["summary"]),
                     details["rights_status"], details["source_reference"], details["source_digest"]),
                )
                outcome = {"status": "saved", "action": action, "profile": _profile(dict(cur.fetchone())), "db_writes": 1}
            else:
                assert profile is not None
                before = _lock_profile(cur, project, subject, profile)
                if action == "approve":
                    if before["status"] != "draft":
                        raise ValueError("PROFILE_NOT_DRAFT")
                    cur.execute(
                        """select 1 from research_subject_profile_version
                           where project_id=%s and subject_id=%s and status='approved' and id<>%s""",
                        (project, subject, profile),
                    )
                    if cur.fetchone() is not None:
                        raise ValueError("RESEARCH_SUBJECT_PROFILE_ALREADY_APPROVED")
                    cur.execute(
                        """update research_subject_profile_version
                           set status='approved', approved_by=%s where id=%s
                           returning id,project_id,subject_id,profile_kind,version_no,status,summary,rights_status,
                                     source_reference,source_digest,content_fingerprint,approved_by,approved_at,
                                     superseded_at,revoked_at,created_at,updated_at""",
                        (actor, profile),
                    )
                    outcome = {"status": "saved", "action": action, "profile": _profile(dict(cur.fetchone())), "db_writes": 1}
                    return _finish(cur, key, outcome)
                if action == "supersede" and before["status"] != "approved":
                    raise ValueError("PROFILE_NOT_APPROVED")
                if action == "revoke" and before["status"] not in {"draft", "approved"}:
                    raise ValueError("PROFILE_NOT_REVOCABLE")
                new_status = "superseded" if action == "supersede" else "revoked"
                cur.execute(
                    """update research_subject_profile_version set status=%s where id=%s
                       returning id,project_id,subject_id,profile_kind,version_no,status,summary,rights_status,
                                 source_reference,source_digest,content_fingerprint,approved_by,approved_at,
                                 superseded_at,revoked_at,created_at,updated_at""",
                    (new_status, profile),
                )
                outcome = {"status": "saved", "action": action, "profile": _profile(dict(cur.fetchone())), "db_writes": 1}
            return _finish(cur, key, outcome)
    except (PermissionError, ValueError):
        raise
    except Exception:
        raise RuntimeError("SUBJECT_PROFILE_ACTION_FAILED") from None
