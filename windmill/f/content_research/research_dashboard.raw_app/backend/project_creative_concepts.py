# py: ==3.14.*
#requirements:
#psycopg[binary]==3.3.6

"""Project-private, append-only original concept drafts.

No operation here creates an action card, approves rights, publishes content,
or calls an external supplier. The project member is derived from Windmill's
end-user identity, never from a caller-provided email.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, TypedDict
from uuid import UUID, uuid4

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
_WRITERS = frozenset({"owner", "admin", "researcher"})
_STATUSES = frozenset({"draft", "ready_for_internal_test", "withdrawn"})
_FIELDS = {
    "title": 160,
    "premise": 1200,
    "character_choice": 1200,
    "episode_payoff": 1200,
    "evidence_note": 1200,
    "test_question": 800,
    "production_constraints": 1200,
}


def _uuid(value: object, name: str) -> UUID:
    if not isinstance(value, str):
        raise ValueError(f"{name} is invalid")
    try:
        return UUID(value)
    except ValueError:
        raise ValueError(f"{name} is invalid") from None


def _actor() -> str:
    actor = os.environ.get("WM_END_USER_EMAIL", "").strip().lower()
    if len(actor) > 254 or not _EMAIL.fullmatch(actor):
        raise PermissionError("RESEARCH_ACTION_IDENTITY_REQUIRED")
    return actor


def _connect(db: postgresql):
    args: dict[str, Any] = {
        "host": db["host"], "port": int(db.get("port", 5432)),
        "user": db["user"], "password": db["password"],
        "dbname": db["dbname"], "sslmode": db.get("sslmode", "prefer"),
    }
    if db.get("options"):
        args["options"] = db["options"]
    return psycopg.connect(**args, row_factory=dict_row)


def _project_member(cur, project: UUID, actor: str, *, write: bool) -> dict[str, Any]:
    cur.execute(
        """select p.name, m.role from research_project p
           join research_organization o on o.id=p.organization_id
           join lateral (
             select member.role, member.status, member.effective_until
               from research_project_member member
              where member.project_id=p.id and member.actor_id=%s
                and member.effective_from<=now()
              order by member.effective_from desc limit 1
           ) m on true
          where p.id=%s and p.status='active' and o.status='active'
            and m.status='active'
            and (m.effective_until is null or m.effective_until>now())"""
        + (" for update of p" if write else ""),
        (actor, project),
    )
    row = cur.fetchone()
    if row is None or (write and row["role"] not in _WRITERS):
        raise PermissionError("RESEARCH_PROJECT_WRITE_DENIED" if write else "RESEARCH_PROJECT_READ_DENIED")
    return dict(row)


def _fields(payload: object, *, revise: bool) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise ValueError("creative concept payload is invalid")
    expected = set(_FIELDS) | {"status"}
    if revise:
        expected |= {"concept_id", "expected_version"}
    if set(payload) != expected:
        raise ValueError("creative concept payload is invalid")
    values: dict[str, str] = {}
    for name, maximum in _FIELDS.items():
        raw = payload[name]
        if not isinstance(raw, str):
            raise ValueError(f"{name} is invalid")
        value = raw.strip()
        if not value or len(value) > maximum:
            raise ValueError(f"{name} is invalid")
        values[name] = value
    if payload["status"] not in _STATUSES:
        raise ValueError("status is invalid")
    values["status"] = payload["status"]
    if not revise and values["status"] == "withdrawn":
        raise ValueError("new creative concept cannot be withdrawn")
    return values


def _replay_or_reserve(cur, key: UUID, actor: str, project: UUID,
                       action: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    digest = hashlib.sha256(json.dumps(
        {"project_id": str(project), "action": action, "payload": payload},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    action_type = f"project_creative.{action}"
    cur.execute(
        """insert into research_user_action(idempotency_key,actor,action_type,payload_hash)
           values (%s,%s,%s,%s) on conflict do nothing returning idempotency_key""",
        (key, actor, action_type, digest),
    )
    if cur.fetchone() is not None:
        return None
    cur.execute(
        "select actor,action_type,payload_hash,outcome from research_user_action where idempotency_key=%s",
        (key,),
    )
    replay = cur.fetchone()
    if (replay is None or replay["actor"] != actor
            or replay["action_type"] != action_type
            or replay["payload_hash"] != digest
            or not isinstance(replay["outcome"], dict)):
        raise ValueError("RESEARCH_ACTION_IDEMPOTENCY_CONFLICT")
    return {**replay["outcome"], "idempotent_replay": True}


def _list(cur, project: UUID, role: str, project_name: str) -> dict[str, Any]:
    cur.execute(
        """select c.id, c.created_by, c.created_at,
                  r.version_no, r.title, r.premise, r.character_choice,
                  r.episode_payoff, r.evidence_note, r.test_question,
                  r.production_constraints, r.status, r.recorded_by, r.recorded_at
             from project_creative_concept c
             join lateral (
               select * from project_creative_concept_revision revision
                where revision.concept_id=c.id
                order by revision.version_no desc limit 1
             ) r on true
            where c.project_id=%s order by c.created_at desc,c.id limit 100""",
        (project,),
    )
    rows = []
    for row in cur.fetchall():
        item = dict(row)
        item["id"] = str(item["id"])
        item["created_at"] = item["created_at"].isoformat()
        item["recorded_at"] = item["recorded_at"].isoformat()
        rows.append(item)
    return {
        "project_name": project_name, "role": role, "concepts": rows,
        "boundary": "原创选题仅为本项目内部提案；机器观察不是完整人工案例，准备试片不代表设定、素材权利或发布已获批准。",
        "truncated": len(rows) == 100,
    }


def _history(cur, project: UUID, concept_id: UUID) -> dict[str, Any]:
    cur.execute(
        "select 1 from project_creative_concept where id=%s and project_id=%s",
        (concept_id, project),
    )
    if cur.fetchone() is None:
        raise PermissionError("RESEARCH_PROJECT_CREATIVE_CONCEPT_DENIED")
    cur.execute(
        """select version_no,title,premise,character_choice,episode_payoff,
                  evidence_note,test_question,production_constraints,status,
                  recorded_by,recorded_at
             from project_creative_concept_revision
            where concept_id=%s and project_id=%s
            order by version_no desc limit 101""",
        (concept_id, project),
    )
    rows = []
    for row in cur.fetchall():
        item = dict(row)
        item["recorded_at"] = item["recorded_at"].isoformat()
        rows.append(item)
    return {"concept_id": str(concept_id), "revisions": rows[:100], "has_more": len(rows) > 100}


def _create(cur, project: UUID, actor: str, values: dict[str, str]) -> dict[str, Any]:
    concept_id = uuid4()
    cur.execute(
        "insert into project_creative_concept(id,project_id,created_by) values (%s,%s,%s)",
        (concept_id, project, actor),
    )
    _insert_revision(cur, project, concept_id, 1, actor, values)
    return {"concept_id": str(concept_id), "version_no": 1, "status": values["status"]}


def _insert_revision(cur, project: UUID, concept_id: UUID, version: int,
                     actor: str, values: dict[str, str]) -> None:
    cur.execute(
        """insert into project_creative_concept_revision
           (concept_id,project_id,version_no,title,premise,character_choice,
            episode_payoff,evidence_note,test_question,production_constraints,
            status,recorded_by)
           values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (concept_id, project, version, values["title"], values["premise"],
         values["character_choice"], values["episode_payoff"],
         values["evidence_note"], values["test_question"],
         values["production_constraints"], values["status"], actor),
    )


def _revise(cur, project: UUID, actor: str, payload: dict[str, Any],
            values: dict[str, str]) -> dict[str, Any]:
    concept_id = _uuid(payload["concept_id"], "concept_id")
    expected_version = payload["expected_version"]
    if type(expected_version) is not int or not 1 <= expected_version < 10000:
        raise ValueError("expected_version is invalid")
    cur.execute(
        "select 1 from project_creative_concept where id=%s and project_id=%s for update",
        (concept_id, project),
    )
    if cur.fetchone() is None:
        raise PermissionError("RESEARCH_PROJECT_CREATIVE_CONCEPT_DENIED")
    cur.execute(
        """select version_no,status from project_creative_concept_revision
            where concept_id=%s and project_id=%s order by version_no desc limit 1""",
        (concept_id, project),
    )
    current = cur.fetchone()
    if current is None or current["version_no"] != expected_version:
        raise ValueError("CREATIVE_CONCEPT_VERSION_CONFLICT")
    if current["status"] == "withdrawn":
        raise ValueError("CREATIVE_CONCEPT_WITHDRAWN")
    version = expected_version + 1
    _insert_revision(cur, project, concept_id, version, actor, values)
    return {"concept_id": str(concept_id), "version_no": version, "status": values["status"]}


def main(db: postgresql, project_id: str, action: str = "list",
         payload: dict[str, Any] | None = None,
         idempotency_key: str | None = None) -> dict[str, Any]:
    project = _uuid(project_id, "project_id")
    actor = _actor()
    if action not in {"list", "history", "create", "revise"}:
        raise ValueError("creative concept action is invalid")
    if action in {"list", "history"}:
        if idempotency_key is not None:
            raise ValueError("read action does not take an idempotency key")
        with _connect(db) as conn, conn.cursor() as cur:
            cur.execute("set transaction read only")
            member = _project_member(cur, project, actor, write=False)
            if action == "list":
                if payload not in (None, {}):
                    raise ValueError("list payload is invalid")
                return _list(cur, project, str(member["role"]), str(member["name"]))
            if not isinstance(payload, dict) or set(payload) != {"concept_id"}:
                raise ValueError("history payload is invalid")
            return _history(cur, project, _uuid(payload["concept_id"], "concept_id"))
    if not isinstance(payload, dict):
        raise ValueError("creative concept payload is invalid")
    key = _uuid(idempotency_key, "idempotency_key")
    values = _fields(payload, revise=action == "revise")
    with _connect(db) as conn, conn.cursor() as cur:
        _project_member(cur, project, actor, write=True)
        replay = _replay_or_reserve(cur, key, actor, project, action, payload)
        if replay is not None:
            return replay
        result = (_create(cur, project, actor, values) if action == "create"
                  else _revise(cur, project, actor, payload, values))
        cur.execute(
            "update research_user_action set outcome=%s where idempotency_key=%s",
            (Jsonb(result), key),
        )
        return result
