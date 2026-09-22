# py: ==3.14.*
#requirements:
#psycopg[binary]==3.3.6

"""Actor-bound, idempotent lifecycle mutations for research briefs."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from typing import TypedDict
from uuid import UUID

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


class postgresql(TypedDict):
    host: str
    port: int
    user: str
    password: str
    dbname: str
    sslmode: str


class ResearchBriefError(ValueError):
    pass


class ResearchBriefConflict(RuntimeError):
    pass


_ACTOR_RE = re.compile(r"^[^\s@]{1,128}@[^\s@]{1,120}$")
_ACTIONS = frozenset({"create", "update", "activate", "pause", "archive"})
_SOURCES = frozenset({"low_fan", "keyword", "account"})
_DEPTHS = frozenset({"metadata", "comments", "media", "review_ready"})
_WINDOWS = frozenset({24, 72, 168, 720})
_CADENCES = frozenset({6, 12, 24})


def _dsn(db: postgresql) -> str:
    return make_conninfo(
        host=db["host"], port=int(db.get("port", 5432)), user=db["user"],
        password=db["password"], dbname=db["dbname"],
        sslmode=db.get("sslmode", "prefer"),
    )


def _actor() -> str:
    value = os.environ.get("WM_END_USER_EMAIL", "").strip().lower()
    if not _ACTOR_RE.fullmatch(value) or len(value) > 254:
        raise PermissionError("RESEARCH_ACTION_IDENTITY_REQUIRED")
    return value


def _authorized_writer(writer_allowlist: str | None) -> str:
    actor = _actor()
    if not isinstance(writer_allowlist, str) or not writer_allowlist.strip():
        raise PermissionError("RESEARCH_ACTION_WRITER_ALLOWLIST_REQUIRED")
    source = writer_allowlist.strip()
    try:
        values = json.loads(source) if source.startswith("[") else re.split(r"[,\n]", source)
    except json.JSONDecodeError:
        raise PermissionError("RESEARCH_ACTION_WRITER_ALLOWLIST_INVALID") from None
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise PermissionError("RESEARCH_ACTION_WRITER_ALLOWLIST_INVALID")
    allowed: set[str] = set()
    for value in values:
        email = value.strip()
        if email != email.lower() or not _ACTOR_RE.fullmatch(email):
            raise PermissionError("RESEARCH_ACTION_WRITER_ALLOWLIST_INVALID")
        allowed.add(email)
    if not allowed:
        raise PermissionError("RESEARCH_ACTION_WRITER_ALLOWLIST_REQUIRED")
    if actor not in allowed:
        raise PermissionError("RESEARCH_ACTION_WRITER_FORBIDDEN")
    return actor


def _uuid4(value: object, *, field: str) -> str:
    try:
        parsed = UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise ResearchBriefError(f"{field} is invalid") from None
    if parsed.version != 4:
        raise ResearchBriefError(f"{field} is invalid")
    return str(parsed)


def _brief_id(value: object) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        raise ResearchBriefError("brief_id is invalid") from None


def _normalized_text(value: object, *, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ResearchBriefError(f"{field} is invalid")
    normalized = " ".join(value.strip().split())
    if not normalized or len(normalized) > maximum or "\x00" in normalized:
        raise ResearchBriefError(f"{field} is invalid")
    return normalized


def _config(
    *, name: object, platform: object, source_type: object, target: object,
    time_window_hours: object, max_items: object, depth: object,
    cadence_hours: object,
) -> dict[str, object]:
    normalized_name = _normalized_text(name, field="name", maximum=80)
    if platform != "douyin" or source_type not in _SOURCES or depth not in _DEPTHS:
        raise ResearchBriefError("brief configuration is invalid")
    if type(time_window_hours) is not int or time_window_hours not in _WINDOWS:
        raise ResearchBriefError("time_window_hours is invalid")
    if type(max_items) is not int or not 1 <= max_items <= 20:
        raise ResearchBriefError("max_items is invalid")
    if depth in {"media", "review_ready"} and max_items > 5:
        raise ResearchBriefError("media scope must not exceed 5 items")
    if cadence_hours in (0, "", None):
        normalized_cadence = None
    elif type(cadence_hours) is int and cadence_hours in _CADENCES:
        normalized_cadence = cadence_hours
    else:
        raise ResearchBriefError("cadence_hours is invalid")
    if source_type == "low_fan":
        normalized_target = None
        if max_items > 5 or time_window_hours == 720:
            raise ResearchBriefError("low_fan scope exceeds its bounded endpoint")
    else:
        normalized_target = _normalized_text(target, field="target", maximum=120)
    return {
        "name": normalized_name,
        "platform": platform,
        "source_type": source_type,
        "target": normalized_target,
        "time_window_hours": time_window_hours,
        "max_items": max_items,
        "depth": depth,
        "cadence_hours": normalized_cadence,
    }


def _payload_hash(value: Mapping[str, object]) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _claim(cur, *, actor: str, action: str, key: str, payload: Mapping[str, object]):
    digest = _payload_hash(payload)
    action_type = f"research_brief.{action}"
    cur.execute(
        """
        insert into research_user_action(idempotency_key, actor, action_type, payload_hash)
        values (%s,%s,%s,%s) on conflict do nothing returning idempotency_key
        """,
        (key, actor, action_type, digest),
    )
    if cur.fetchone() is not None:
        return None
    cur.execute(
        """
        select actor, action_type, payload_hash, outcome from research_user_action
        where idempotency_key=%s
        """,
        (key,),
    )
    row = cur.fetchone()
    if (
        row is None or row["actor"] != actor or row["action_type"] != action_type
        or row["payload_hash"] != digest
    ):
        raise ResearchBriefConflict("RESEARCH_ACTION_IDEMPOTENCY_CONFLICT")
    if not isinstance(row["outcome"], Mapping):
        raise ResearchBriefConflict("RESEARCH_ACTION_RECONCILIATION_REQUIRED")
    replay = dict(row["outcome"])
    replay.update(idempotent_replay=True, db_writes=0)
    return replay


def _finish(cur, key: str, outcome: dict[str, object]):
    stored = {k: v for k, v in outcome.items() if k not in {"idempotent_replay", "db_writes"}}
    cur.execute(
        "update research_user_action set outcome=%s where idempotency_key=%s",
        (Jsonb(stored), key),
    )
    return outcome


def _mutate(
    db: postgresql, *, actor: str, action: str, idempotency_key: str,
    brief_id: str, name: str, platform: str, source_type: str, target: str,
    time_window_hours: int, max_items: int, depth: str,
    cadence_hours: int | None,
):
    if action not in _ACTIONS:
        raise ResearchBriefError("action is invalid")
    key = _uuid4(idempotency_key, field="idempotency_key")
    normalized_id = "" if action == "create" else _brief_id(brief_id)
    payload: dict[str, object] = {"action": action, "brief_id": normalized_id}
    config: dict[str, object] | None = None
    if action in {"create", "update"}:
        config = _config(
            name=name, platform=platform, source_type=source_type, target=target,
            time_window_hours=time_window_hours, max_items=max_items, depth=depth,
            cadence_hours=cadence_hours,
        )
        payload["config"] = config

    with psycopg.connect(_dsn(db), row_factory=dict_row) as conn, conn.cursor() as cur:
        replay = _claim(cur, actor=actor, action=action, key=key, payload=payload)
        if replay is not None:
            return replay
        if action == "create":
            assert config is not None
            cur.execute(
                """
                insert into research_brief(
                  owner_actor, name, platform, source_type, target,
                  time_window_hours, max_items, depth, cadence_hours
                ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                returning id, status, config_version
                """,
                (
                    actor, config["name"], config["platform"], config["source_type"],
                    config["target"], config["time_window_hours"], config["max_items"],
                    config["depth"], config["cadence_hours"],
                ),
            )
        elif action == "update":
            assert config is not None
            cur.execute(
                """
                update research_brief set
                  name=%s, platform=%s, source_type=%s, target=%s,
                  time_window_hours=%s, max_items=%s, depth=%s, cadence_hours=%s,
                  config_version=config_version+1, updated_at=now()
                where id=%s and owner_actor=%s and status in ('draft','paused')
                returning id, status, config_version
                """,
                (
                    config["name"], config["platform"], config["source_type"],
                    config["target"], config["time_window_hours"], config["max_items"],
                    config["depth"], config["cadence_hours"], normalized_id, actor,
                ),
            )
        elif action == "activate":
            cur.execute(
                """
                update research_brief set status='active', next_due_at=now(), updated_at=now()
                where id=%s and owner_actor=%s and status in ('draft','paused')
                returning id, status, config_version
                """,
                (normalized_id, actor),
            )
        elif action == "pause":
            cur.execute(
                """
                update research_brief set status='paused', next_due_at=null, updated_at=now()
                where id=%s and owner_actor=%s and status='active'
                returning id, status, config_version
                """,
                (normalized_id, actor),
            )
        else:
            cur.execute(
                """
                update research_brief set status='archived', next_due_at=null, updated_at=now()
                where id=%s and owner_actor=%s and status <> 'archived'
                returning id, status, config_version
                """,
                (normalized_id, actor),
            )
        row = cur.fetchone()
        if row is None:
            raise ResearchBriefConflict("RESEARCH_BRIEF_STATE_CONFLICT")
        outcome = {
            "status": "saved",
            "action": action,
            "brief_id": str(row["id"]),
            "brief_status": row["status"],
            "config_version": row["config_version"],
            "idempotent_replay": False,
            "db_writes": 1,
            "external_calls": 0,
            "llm_calls": 0,
        }
        return _finish(cur, key, outcome)


def main(
    db: postgresql,
    action: str,
    idempotency_key: str,
    brief_id: str = "",
    name: str = "",
    platform: str = "douyin",
    source_type: str = "low_fan",
    target: str = "",
    time_window_hours: int = 24,
    max_items: int = 5,
    depth: str = "metadata",
    cadence_hours: int | None = None,
    writer_allowlist: str = "",
):
    try:
        return _mutate(
            db, actor=_authorized_writer(writer_allowlist), action=action,
            idempotency_key=idempotency_key, brief_id=brief_id, name=name,
            platform=platform, source_type=source_type, target=target,
            time_window_hours=time_window_hours, max_items=max_items, depth=depth,
            cadence_hours=cadence_hours,
        )
    except (PermissionError, ResearchBriefConflict, ResearchBriefError):
        raise
    except Exception:
        raise RuntimeError("RESEARCH_BRIEF_ACTION_FAILED") from None
