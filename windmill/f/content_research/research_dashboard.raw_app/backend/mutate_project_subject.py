# py: ==3.14.*
#requirements:
#psycopg[binary]==3.3.6

"""Actor-bound subject, rule-term, and relevance-correction mutations."""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Iterable, TypedDict
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
_SUBJECT_TYPES = frozenset({"destination", "ip", "character", "product", "activity", "brand", "account", "topic", "other"})
_TERM_TYPES = ("alias", "geographic_context", "exclusion")
_DECISIONS = frozenset({"pending", "relevant", "irrelevant"})
_RULE_VERSION = "subject-relevance-v1.0.0"


def _actor() -> str:
    actor = os.environ.get("WM_END_USER_EMAIL", "").strip().lower()
    if not _EMAIL.fullmatch(actor) or len(actor) > 254:
        raise PermissionError("RESEARCH_ACTION_IDENTITY_REQUIRED")
    return actor


def _uuid(value: object, field: str) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise ValueError(f"{field.upper()}_INVALID") from None


def _text(value: object, field: str, limit: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field.upper()}_INVALID")
    normalized = " ".join(value.strip().split())
    if not normalized or len(normalized) > limit or "\x00" in normalized:
        raise ValueError(f"{field.upper()}_INVALID")
    return normalized


def _terms(value: object, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 30:
        raise ValueError(f"{field.upper()}_INVALID")
    return list(dict.fromkeys(_text(item, field, 160) for item in value))


def _connect(db: postgresql):
    options: dict[str, Any] = {
        "host": db["host"], "port": int(db.get("port", 5432)),
        "user": db["user"], "password": db["password"],
        "dbname": db["dbname"], "sslmode": db.get("sslmode", "prefer"),
    }
    if db.get("options"):
        options["options"] = db["options"]
    return psycopg.connect(**options, row_factory=dict_row)


def _writer(cur, project: UUID, actor: str) -> None:
    cur.execute(
        """
        select 1 from research_project project
        join research_organization organization on organization.id=project.organization_id
        join lateral (
          select role, status, effective_until from research_project_member
          where project_id=project.id and actor_id=%s and effective_from<=now()
          order by effective_from desc limit 1
        ) member on true
        where project.id=%s and project.status='active' and organization.status='active'
          and member.status='active' and member.role in ('owner','admin','researcher')
          and (member.effective_until is null or member.effective_until>now())
        for update of project
        """,
        (actor, project),
    )
    if cur.fetchone() is None:
        raise PermissionError("RESEARCH_PROJECT_MANAGE_DENIED")


def _claim(cur, *, key: UUID, actor: str, action: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    action_type = f"project_subject.{action}"
    cur.execute(
        """insert into research_user_action(idempotency_key,actor,action_type,payload_hash)
           values (%s,%s,%s,%s) on conflict do nothing returning idempotency_key""",
        (key, actor, action_type, digest),
    )
    if cur.fetchone() is not None:
        return None
    cur.execute("select actor, action_type, payload_hash, outcome from research_user_action where idempotency_key=%s", (key,))
    previous = cur.fetchone()
    if not previous or previous["actor"] != actor or previous["action_type"] != action_type or previous["payload_hash"] != digest or not isinstance(previous["outcome"], dict):
        raise ValueError("RESEARCH_ACTION_IDEMPOTENCY_CONFLICT")
    return {**previous["outcome"], "idempotent_replay": True, "db_writes": 0}


def _finish(cur, key: UUID, outcome: dict[str, Any]) -> dict[str, Any]:
    cur.execute("update research_user_action set outcome=%s where idempotency_key=%s", (Jsonb(outcome), key))
    return outcome


def _replace_terms(cur, *, project: UUID, subject: UUID, groups: dict[str, list[str]]) -> None:
    for term_type, values in groups.items():
        cur.execute(
            """update research_subject_term set status='archived', updated_at=now()
               where project_id=%s and subject_id=%s and term_type=%s and status='active'
                 and not (term=any(%s::text[]))""",
            (project, subject, term_type, values),
        )
        for term in values:
            cur.execute(
                """insert into research_subject_term(project_id,subject_id,term,term_type)
                   values (%s,%s,%s,%s)
                   on conflict (subject_id,term_type,lower(term)) where status='active'
                   do update set updated_at=now()""",
                (project, subject, term, term_type),
            )


def main(
    db: postgresql, project_id: str, action: str, idempotency_key: str,
    subject_id: str = "", name: str = "", subject_type: str = "topic",
    aliases: list[str] | None = None, geographic_contexts: list[str] | None = None,
    exclusions: list[str] | None = None, video_id: str = "", decision: str = "",
    reason: str = "",
):
    if action not in {"create_subject", "update_terms", "correct_relevance"}:
        raise ValueError("ACTION_INVALID")
    actor, project, key = _actor(), _uuid(project_id, "project_id"), _uuid(idempotency_key, "idempotency_key")
    groups = {
        "alias": _terms(aliases, "aliases"),
        "geographic_context": _terms(geographic_contexts, "geographic_contexts"),
        "exclusion": _terms(exclusions, "exclusions"),
    }
    if action == "create_subject":
        name = _text(name, "name", 160)
        if subject_type not in _SUBJECT_TYPES:
            raise ValueError("SUBJECT_TYPE_INVALID")
    else:
        subject = _uuid(subject_id, "subject_id")
    if action == "correct_relevance":
        video = _uuid(video_id, "video_id")
        if decision not in _DECISIONS:
            raise ValueError("DECISION_INVALID")
        reason = _text(reason, "reason", 500)
    payload: dict[str, Any] = {"project_id": str(project), "action": action, "subject_id": subject_id}
    if action in {"create_subject", "update_terms"}:
        payload["terms"] = groups
    if action == "create_subject":
        payload.update(name=name, subject_type=subject_type)
    if action == "correct_relevance":
        payload.update(video_id=str(video), decision=decision, reason=reason)
    try:
        with _connect(db) as conn, conn.cursor() as cur:
            _writer(cur, project, actor)
            replay = _claim(cur, key=key, actor=actor, action=action, payload=payload)
            if replay is not None:
                return replay
            if action == "create_subject":
                cur.execute(
                    """insert into research_subject(project_id,name,subject_type)
                       values (%s,%s,%s) returning id""", (project, name, subject_type),
                )
                subject = cur.fetchone()["id"]
                _replace_terms(cur, project=project, subject=subject, groups=groups)
                outcome = {"status": "saved", "action": action, "subject_id": str(subject), "db_writes": 1}
            elif action == "update_terms":
                cur.execute("select 1 from research_subject where id=%s and project_id=%s and status='active' for update", (subject, project))
                if cur.fetchone() is None:
                    raise ValueError("SUBJECT_NOT_FOUND")
                _replace_terms(cur, project=project, subject=subject, groups=groups)
                outcome = {"status": "saved", "action": action, "subject_id": str(subject), "db_writes": 1}
            else:
                cur.execute(
                    """select decision, run_id from project_video_subject_relevance
                       where project_id=%s and subject_id=%s and video_id=%s for update""",
                    (project, subject, video),
                )
                old = cur.fetchone()
                if old is None:
                    raise ValueError("RELEVANCE_NOT_FOUND")
                cur.execute(
                    """update project_video_subject_relevance set decision=%s, decision_source='manual',
                         reviewed_by=%s, reviewed_at=now(), updated_at=now()
                       where project_id=%s and subject_id=%s and video_id=%s""",
                    (decision, actor, project, subject, video),
                )
                cur.execute(
                    """insert into project_video_subject_relevance_audit(
                         project_id,video_id,subject_id,event_type,actor,prior_decision,decision,reason,rule_version,run_id
                       ) values (%s,%s,%s,'manual_override',%s,%s,%s,%s,%s,%s)""",
                    (project, video, subject, actor, old["decision"], decision, reason, _RULE_VERSION, old["run_id"]),
                )
                outcome = {"status": "saved", "action": action, "subject_id": str(subject), "video_id": str(video), "decision": decision, "db_writes": 2}
            return _finish(cur, key, outcome)
    except (PermissionError, ValueError):
        raise
    except Exception:
        raise RuntimeError("PROJECT_SUBJECT_ACTION_FAILED") from None
