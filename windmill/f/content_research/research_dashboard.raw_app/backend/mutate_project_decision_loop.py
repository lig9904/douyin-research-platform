# py: ==3.14.*
#requirements:
#psycopg[binary]==3.3.6

"""Bounded, idempotent project decision-loop mutations."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date, datetime, timezone
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
_ACTIONS = frozenset({"create_card", "update_card", "set_card_status", "record_review", "create_publication", "record_daily_metric"})
_DECISIONS = frozenset({"adopt", "observe", "exclude"})
_CARD_STATUSES = frozenset({"draft", "active", "observing", "adopted", "excluded", "reviewed", "archived"})
_PUBLICATION_STATUSES = frozenset({"draft", "published", "archived"})
_WRITER_ROLES = frozenset({"owner", "admin", "researcher"})
_TEXT_FIELDS = frozenset({"hypothesis", "reference_point", "adaptation_difference"})
_METRIC_FIELDS = frozenset({"impressions", "engagements", "likes", "comments", "shares", "follows", "conversions"})
_NEXT_CARD_STATUS = {
    "active": frozenset({"adopted", "archived"}),
    "observing": frozenset({"archived"}),
    "adopted": frozenset({"archived"}),
    "excluded": frozenset({"archived"}),
}


def _actor() -> str:
    actor = os.environ.get("WM_END_USER_EMAIL", "").strip().lower()
    if len(actor) > 254 or not _EMAIL.fullmatch(actor):
        raise PermissionError("RESEARCH_ACTION_IDENTITY_REQUIRED")
    return actor


def _uuid(value: object, name: str) -> UUID:
    if not isinstance(value, str):
        raise ValueError(f"{name} is invalid")
    try:
        return UUID(value)
    except ValueError:
        raise ValueError(f"{name} is invalid") from None


def _text(value: object, name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} is invalid")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{name} is invalid")
    return normalized


def _email(value: object, name: str = "owner_actor") -> str:
    normalized = _text(value, name, 254).lower()
    if not _EMAIL.fullmatch(normalized):
        raise ValueError(f"{name} is invalid")
    return normalized


def _date(value: object, name: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{name} is invalid")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{name} is invalid") from None


def _timestamp(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{name} is invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must include timezone")
    return parsed


def _metric(value: object, name: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0 or value > 9_000_000_000_000_000_000:
        raise ValueError(f"{name} is invalid")
    return value


def _connect(db: postgresql):
    args: dict[str, Any] = {
        "host": db["host"], "port": int(db.get("port", 5432)),
        "user": db["user"], "password": db["password"],
        "dbname": db["dbname"], "sslmode": db.get("sslmode", "prefer"),
    }
    if db.get("options"):
        args["options"] = db["options"]
    return psycopg.connect(**args, row_factory=dict_row)


def _writer(cur, project: UUID, actor: str) -> str:
    cur.execute(
        """select m.role from research_project p
           join research_organization o on o.id=p.organization_id
           join lateral (
             select member.role, member.status, member.effective_until
             from research_project_member member
             where member.project_id=p.id and member.actor_id=%s and member.effective_from<=now()
             order by member.effective_from desc limit 1
           ) m on true
           where p.id=%s and p.status='active' and o.status='active'
             and m.status='active' and (m.effective_until is null or m.effective_until>now())
           for update of p""",
        (actor, project),
    )
    row = cur.fetchone()
    if row is None or row["role"] not in _WRITER_ROLES:
        raise PermissionError("RESEARCH_PROJECT_WRITE_DENIED")
    return str(row["role"])


def _active_member(cur, project: UUID, actor: str) -> bool:
    cur.execute(
        """select 1 from research_project_member member
           where member.project_id=%s and member.actor_id=%s and member.effective_from<=now()
             and member.status='active'
             and (member.effective_until is null or member.effective_until>now())
             and not exists (
               select 1 from research_project_member newer
               where newer.project_id=member.project_id and newer.actor_id=member.actor_id
                 and newer.effective_from<=now() and newer.effective_from>member.effective_from
             )""",
        (project, actor),
    )
    return cur.fetchone() is not None


def _begin_action(cur, key: UUID, actor: str, action: str, project: UUID, payload: dict[str, Any]) -> dict[str, Any] | None:
    digest = hashlib.sha256(json.dumps(
        {"project_id": str(project), "action": action, "payload": payload},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    action_type = f"project_decision.{action}"
    cur.execute(
        """insert into research_user_action(idempotency_key,actor,action_type,payload_hash)
           values (%s,%s,%s,%s) on conflict do nothing returning idempotency_key""",
        (key, actor, action_type, digest),
    )
    if cur.fetchone() is not None:
        return None
    cur.execute("select actor, action_type, payload_hash, outcome from research_user_action where idempotency_key=%s", (key,))
    replay = cur.fetchone()
    if not replay or replay["actor"] != actor or replay["action_type"] != action_type or replay["payload_hash"] != digest or not isinstance(replay["outcome"], dict):
        raise ValueError("RESEARCH_ACTION_IDEMPOTENCY_CONFLICT")
    return {**replay["outcome"], "idempotent_replay": True}


def _finish_action(cur, key: UUID, outcome: dict[str, Any]) -> dict[str, Any]:
    cur.execute("update research_user_action set outcome=%s where idempotency_key=%s", (Jsonb(outcome), key))
    return outcome


def _card(cur, project: UUID, card_id: object) -> dict[str, Any]:
    card = _uuid(card_id, "card_id")
    cur.execute(
        """select c.id, c.source_video_id, c.hypothesis, c.reference_point, c.adaptation_difference,
                  c.owner_actor, c.decision, c.status, c.subject_id from project_decision_card c
           where c.id=%s and c.project_id=%s for update of c""",
        (card, project),
    )
    row = cur.fetchone()
    if row is None:
        raise PermissionError("RESEARCH_PROJECT_DECISION_CARD_DENIED")
    return dict(row)


def _record_event(cur, project: UUID, card: UUID, actor: str, action: str,
                  changed: list[str], before: str | None = None, after: str | None = None) -> None:
    cur.execute(
        """insert into project_decision_card_event
           (project_id,decision_card_id,actor_id,action,from_status,to_status,changed_fields)
           values (%s,%s,%s,%s,%s,%s,%s)""",
        (project, card, actor, action, before, after, changed),
    )


def _create_card(cur, project: UUID, actor: str, role: str, payload: dict[str, Any]) -> dict[str, Any]:
    if set(payload) != {"source_video_id", "subject_id", "profile_id", "hypothesis", "reference_point", "adaptation_difference", "owner_actor", "decision"}:
        raise ValueError("create_card payload is invalid")
    video = _uuid(payload["source_video_id"], "source_video_id")
    decision = payload["decision"]
    if decision not in _DECISIONS:
        raise ValueError("decision is invalid")
    status = {"adopt": "active", "observe": "observing", "exclude": "excluded"}[decision]
    subject = None if payload["subject_id"] is None else _uuid(payload["subject_id"], "subject_id")
    profile = None if payload["profile_id"] is None else _uuid(payload["profile_id"], "profile_id")
    if decision == "adopt":
        if role not in {"owner", "admin"}:
            raise PermissionError("RESEARCH_PROJECT_ADOPTION_DENIED")
        if subject is None or profile is None:
            raise ValueError("adopt requires an approved subject profile")
    elif profile is not None:
        raise ValueError("only adopt may bind a subject profile")
    if subject is not None:
        cur.execute("select 1 from research_subject where id=%s and project_id=%s and status='active'", (subject, project))
        if cur.fetchone() is None:
            raise ValueError("subject_id is invalid")
    if profile is not None:
        cur.execute(
            """select 1 from research_subject_profile_version
               where id=%s and project_id=%s and subject_id=%s
                 and status='approved' and rights_status='cleared'
               for share""",
            (profile, project, subject),
        )
        if cur.fetchone() is None:
            raise ValueError("profile_id must be the current approved, rights-cleared subject profile")
    values = {
        "hypothesis": _text(payload["hypothesis"], "hypothesis", 1200),
        "reference_point": _text(payload["reference_point"], "reference_point", 1200),
        "adaptation_difference": _text(payload["adaptation_difference"], "adaptation_difference", 1200),
        "owner_actor": _email(payload["owner_actor"]),
    }
    if not _active_member(cur, project, values["owner_actor"]):
        raise ValueError("owner_actor must be an active project member")
    cur.execute(
        """insert into project_decision_card
           (project_id,source_video_id,subject_id,hypothesis,reference_point,adaptation_difference,
            owner_actor,decision,status,created_by)
           values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
        (project, video, subject, values["hypothesis"], values["reference_point"], values["adaptation_difference"],
         values["owner_actor"], decision, status, actor),
    )
    card = cur.fetchone()["id"]
    if profile is not None:
        cur.execute(
            """insert into project_decision_card_profile_binding
               (project_id,decision_card_id,subject_id,profile_id,bound_by)
               values (%s,%s,%s,%s,%s)""",
            (project, card, subject, profile, actor),
        )
    _record_event(cur, project, card, actor, "created", ["subject_id", "hypothesis", "reference_point", "adaptation_difference", "owner_actor", "decision"], None, status)
    return {"changed": True, "card_id": str(card), "status": status}


def _update_card(cur, project: UUID, actor: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload.get("changes"), dict) or set(payload) != {"card_id", "changes"}:
        raise ValueError("update_card payload is invalid")
    card = _card(cur, project, payload["card_id"])
    changes = payload["changes"]
    if card["status"] in {"reviewed", "archived"}:
        raise ValueError("a reviewed or archived card is immutable; create a follow-up card for a new cycle")
    if not changes or not set(changes).issubset(_TEXT_FIELDS | {"owner_actor", "subject_id"}):
        raise ValueError("card changes are invalid")
    normalized: dict[str, Any] = {}
    for name in _TEXT_FIELDS:
        if name in changes:
            normalized[name] = _text(changes[name], name, 1200)
    if "owner_actor" in changes:
        normalized["owner_actor"] = _email(changes["owner_actor"])
        if not _active_member(cur, project, normalized["owner_actor"]):
            raise ValueError("owner_actor must be an active project member")
    if "subject_id" in changes:
        subject = None if changes["subject_id"] is None else _uuid(changes["subject_id"], "subject_id")
        if subject is not None:
            cur.execute("select 1 from research_subject where id=%s and project_id=%s and status='active'", (subject, project))
            if cur.fetchone() is None:
                raise ValueError("subject_id is invalid")
        normalized["subject_id"] = subject
    actual = {name: value for name, value in normalized.items() if card[name] != value}
    if not actual:
        return {"changed": False, "card_id": str(card["id"]), "status": card["status"]}
    assignments = ", ".join(f"{name}=%s" for name in actual)
    cur.execute(f"update project_decision_card set {assignments}, updated_at=now() where id=%s", (*actual.values(), card["id"]))
    _record_event(cur, project, card["id"], actor, "updated", sorted(actual), None, None)
    return {"changed": True, "card_id": str(card["id"]), "status": card["status"]}


def _set_card_status(cur, project: UUID, actor: str, payload: dict[str, Any]) -> dict[str, Any]:
    if set(payload) != {"card_id", "status"} or payload["status"] not in _CARD_STATUSES or payload["status"] == "reviewed":
        raise ValueError("set_card_status payload is invalid")
    card = _card(cur, project, payload["card_id"])
    next_status = payload["status"]
    if card["status"] in {"reviewed", "archived"}:
        raise ValueError("a reviewed or archived card is immutable; create a follow-up card for a new cycle")
    if next_status not in _NEXT_CARD_STATUS.get(card["status"], frozenset()):
        raise ValueError("card status transition is invalid; create a follow-up card for a new cycle")
    if next_status == card["status"]:
        return {"changed": False, "card_id": str(card["id"]), "status": next_status}
    cur.execute("update project_decision_card set status=%s, updated_at=now() where id=%s", (next_status, card["id"]))
    _record_event(cur, project, card["id"], actor, "status_changed", ["status"], card["status"], next_status)
    return {"changed": True, "card_id": str(card["id"]), "status": next_status}


def _record_review(cur, project: UUID, actor: str, payload: dict[str, Any]) -> dict[str, Any]:
    if set(payload) != {"card_id", "observation_id", "conclusion", "evidence", "next_action"}:
        raise ValueError("record_review payload is invalid")
    card = _card(cur, project, payload["card_id"])
    if card["status"] in {"reviewed", "archived"}:
        raise ValueError("a reviewed or archived card cannot be reviewed again")
    observation = _uuid(payload["observation_id"], "observation_id")
    cur.execute(
        """select metric.id, metric.version, metric.metric_date, metric.impressions,
                  metric.engagements, metric.likes, metric.comments, metric.shares,
                  metric.follows, metric.conversions, metric.source, metric.measurement_scope,
                  metric.source_reference, metric.source_reported_at, metric.source_version_or_digest,
                  metric.recorded_at,
                  jsonb_build_object(
                    'id', metric.id, 'version', metric.version, 'metric_date', metric.metric_date,
                    'impressions', metric.impressions, 'engagements', metric.engagements,
                    'likes', metric.likes, 'comments', metric.comments, 'shares', metric.shares,
                    'follows', metric.follows, 'conversions', metric.conversions,
                    'source', metric.source, 'source_reference', metric.source_reference,
                    'source_reported_at', metric.source_reported_at,
                    'source_version_or_digest', metric.source_version_or_digest,
                    'measurement_scope', metric.measurement_scope, 'recorded_at', metric.recorded_at
                  ) as snapshot
           from project_publication_metric_observation metric
           join project_publication_record publication
             on publication.id=metric.publication_id and publication.project_id=metric.project_id
           where metric.id=%s and metric.project_id=%s and publication.decision_card_id=%s
             and publication.status='published'
           for key share of metric""",
        (observation, project, card["id"]),
    )
    observation_row = cur.fetchone()
    if observation_row is None:
        raise ValueError("an observation for this card's published record is required before review")
    conclusion = _text(payload["conclusion"], "conclusion", 2000)
    evidence = _text(payload["evidence"], "evidence", 1200)
    next_action = _text(payload["next_action"], "next_action", 800)
    cur.execute(
        """update project_decision_card set status='reviewed', review_conclusion=%s,
             review_evidence=%s, next_action=%s, reviewed_by=%s, reviewed_at=now(),
             review_observation_id=%s, review_observation_version=%s,
             review_metric_snapshot=%s, updated_at=now()
           where id=%s""",
        (conclusion, evidence, next_action, actor, observation_row["id"], observation_row["version"],
         Jsonb(observation_row["snapshot"]), card["id"]),
    )
    _record_event(cur, project, card["id"], actor, "reviewed", ["review_conclusion", "review_evidence", "next_action", "status"], card["status"], "reviewed")
    return {"changed": True, "card_id": str(card["id"]), "status": "reviewed"}


def _create_publication(cur, project: UUID, actor: str, payload: dict[str, Any]) -> dict[str, Any]:
    if set(payload) != {"decision_card_id", "publication_date", "title", "content_reference"}:
        raise ValueError("create_publication payload is invalid")
    card = _card(cur, project, payload["decision_card_id"])
    cur.execute(
        """insert into project_publication_record
           (project_id,decision_card_id,publication_date,title,content_reference,status,created_by)
           values (%s,%s,%s,%s,%s,'published',%s) returning id""",
        (project, card["id"], _date(payload["publication_date"], "publication_date"),
         _text(payload["title"], "title", 160), _text(payload["content_reference"], "content_reference", 512), actor),
    )
    return {"changed": True, "publication_id": str(cur.fetchone()["id"]), "status": "published"}


def _record_daily_metric(cur, project: UUID, actor: str, payload: dict[str, Any]) -> dict[str, Any]:
    if set(payload) != {"publication_id", "metric_date", "metrics", "source", "source_reference", "source_reported_at", "source_version_or_digest", "measurement_scope"} or not isinstance(payload["metrics"], dict):
        raise ValueError("record_daily_metric payload is invalid")
    metrics = payload["metrics"]
    if set(metrics) != _METRIC_FIELDS:
        raise ValueError("daily metrics are invalid")
    publication = _uuid(payload["publication_id"], "publication_id")
    metric_day = _date(payload["metric_date"], "metric_date")
    cur.execute(
        """select publication_date from project_publication_record
           where id=%s and project_id=%s and status='published' for update""",
        (publication, project),
    )
    record = cur.fetchone()
    if record is None:
        raise PermissionError("RESEARCH_PROJECT_PUBLICATION_DENIED")
    if metric_day < record["publication_date"]:
        raise ValueError("metric_date cannot precede publication_date")
    if metric_day > datetime.now(timezone.utc).date():
        raise ValueError("metric_date cannot be in the future")
    values = {name: _metric(metrics[name], name) for name in _METRIC_FIELDS}
    if all(value is None for value in values.values()):
        raise ValueError("at least one daily metric must be known")
    source = payload["source"]
    if source not in {"manual", "imported_aggregate"}:
        raise ValueError("metric source is invalid")
    scope = _text(payload["measurement_scope"], "measurement_scope", 300)
    source_reference = _text(payload["source_reference"], "source_reference", 512)
    source_reported_at = _timestamp(payload["source_reported_at"], "source_reported_at")
    source_version_or_digest = _text(payload["source_version_or_digest"], "source_version_or_digest", 256)
    cur.execute(
        """select coalesce(max(version),0)+1 as next_version
           from project_publication_metric_observation
           where publication_id=%s and metric_date=%s""",
        (publication, metric_day),
    )
    version = cur.fetchone()["next_version"]
    cur.execute(
        """insert into project_publication_metric_observation
           (project_id,publication_id,metric_date,version,impressions,engagements,likes,comments,shares,follows,conversions,source,source_reference,source_reported_at,source_version_or_digest,measurement_scope,recorded_by)
           values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
        (project, publication, metric_day, version, values["impressions"], values["engagements"], values["likes"],
         values["comments"], values["shares"], values["follows"], values["conversions"], source,
         source_reference, source_reported_at, source_version_or_digest, scope, actor),
    )
    return {"changed": True, "publication_id": str(publication), "metric_date": metric_day.isoformat(), "observation_id": str(cur.fetchone()["id"]), "version": version}


def main(db: postgresql, project_id: str, action: str, payload: dict[str, Any], idempotency_key: str):
    actor = _actor()
    project = _uuid(project_id, "project_id")
    key = _uuid(idempotency_key, "idempotency_key")
    if action not in _ACTIONS or not isinstance(payload, dict):
        raise ValueError("action is invalid")
    try:
        with _connect(db) as conn, conn.cursor() as cur:
            role = _writer(cur, project, actor)
            replay = _begin_action(cur, key, actor, action, project, payload)
            if replay is not None:
                return replay
            if action == "create_card":
                outcome = _create_card(cur, project, actor, role, payload)
            elif action == "update_card":
                outcome = _update_card(cur, project, actor, payload)
            elif action == "set_card_status":
                outcome = _set_card_status(cur, project, actor, payload)
            elif action == "record_review":
                outcome = _record_review(cur, project, actor, payload)
            elif action == "create_publication":
                outcome = _create_publication(cur, project, actor, payload)
            else:
                outcome = _record_daily_metric(cur, project, actor, payload)
            return _finish_action(cur, key, outcome)
    except (PermissionError, ValueError):
        raise
    except Exception:
        raise RuntimeError("RESEARCH_PROJECT_DECISION_LOOP_MUTATION_UNAVAILABLE") from None
