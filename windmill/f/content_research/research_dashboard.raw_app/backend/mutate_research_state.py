#requirements:
#psycopg[binary]==3.3.6

"""Bounded, actor-audited write operations for the research dashboard."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from typing import Any, TypedDict
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


class ResearchActionError(ValueError):
    pass


class ResearchActionConflict(RuntimeError):
    pass


_ACTOR_RE = re.compile(r"^[^\s@]{1,128}@[^\s@]{1,120}$")
_ASSETS = {
    "video": ("source_video", "video_id"),
    "account": ("source_account", "account_id"),
    "signal": ("external_signal", "signal_id"),
}
_MONITORING_STATUSES = {
    "untracked",
    "observe",
    "monitoring",
    "follow_up",
    "selected",
    "stopped",
}
_FILTER_KEYS = {
    "videos": {
        "platform", "days", "research_level", "source_type", "priority_min",
        "status", "play_min", "play_max", "follower_min", "follower_max",
        "collected", "query", "sort", "page_size",
    },
    "accounts": {
        "platform", "days", "research_level", "status", "follower_min",
        "follower_max", "query", "sort", "page_size",
    },
    "hotspots": {
        "platform", "days", "signal_type", "research_level", "status",
        "priority_min", "query", "sort", "page_size",
    },
}


def _dsn(db: postgresql) -> str:
    return make_conninfo(
        host=db["host"],
        port=int(db.get("port", 5432)),
        user=db["user"],
        password=db["password"],
        dbname=db["dbname"],
        sslmode=db.get("sslmode", "prefer"),
    )


def _actor() -> str:
    value = os.environ.get("WM_END_USER_EMAIL", "").strip().lower()
    if not _ACTOR_RE.fullmatch(value) or len(value) > 254:
        raise PermissionError("RESEARCH_ACTION_IDENTITY_REQUIRED")
    return value


def _authorized_writer(writer_allowlist: str | None) -> str:
    """Return the authenticated actor only when local/server policy permits writes.

    App backends run using the publisher's database resource, so resource ACLs
    alone cannot prove that a Viewer did not invoke this endpoint.  The runtime
    allowlist is deliberately server-managed and never supplied by the caller.
    """

    actor = _actor()
    if not isinstance(writer_allowlist, str) or not writer_allowlist.strip():
        raise PermissionError("RESEARCH_ACTION_WRITER_ALLOWLIST_REQUIRED")
    source = writer_allowlist.strip()
    if source.startswith("["):
        try:
            values = json.loads(source)
        except json.JSONDecodeError:
            raise PermissionError("RESEARCH_ACTION_WRITER_ALLOWLIST_INVALID") from None
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise PermissionError("RESEARCH_ACTION_WRITER_ALLOWLIST_INVALID")
    else:
        values = re.split(r"[,\n]", source)

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
        raise ResearchActionError(f"{field} is invalid") from None
    if parsed.version != 4:
        raise ResearchActionError(f"{field} is invalid")
    return str(parsed)


def _asset_ids(values: object) -> list[str]:
    if not isinstance(values, list) or not 1 <= len(values) <= 50:
        raise ResearchActionError("asset_ids must contain between 1 and 50 UUIDs")
    result = sorted({_uuid4(value, field="asset_id") for value in values})
    if len(result) != len(values):
        raise ResearchActionError("asset_ids must be unique")
    return result


def _text(value: object, *, field: str, maximum: int, required: bool = True) -> str:
    if not isinstance(value, str):
        raise ResearchActionError(f"{field} is invalid")
    normalized = " ".join(value.strip().split())
    if (required and not normalized) or len(normalized) > maximum or "\x00" in normalized:
        raise ResearchActionError(f"{field} is invalid")
    return normalized


def _filters(view_key: object, filters_json: object) -> tuple[str, dict[str, object]]:
    if view_key not in _FILTER_KEYS:
        raise ResearchActionError("view_key is invalid")
    if not isinstance(filters_json, str) or len(filters_json.encode("utf-8")) > 4096:
        raise ResearchActionError("filters_json is invalid")
    try:
        parsed = json.loads(filters_json)
    except json.JSONDecodeError:
        raise ResearchActionError("filters_json is invalid") from None
    if not isinstance(parsed, dict) or not set(parsed).issubset(_FILTER_KEYS[str(view_key)]):
        raise ResearchActionError("filters_json is invalid")
    if any(isinstance(value, (dict, list)) for value in parsed.values()):
        raise ResearchActionError("filters_json is invalid")
    for key, value in parsed.items():
        if isinstance(value, str):
            maximum = 200 if key == "query" else 256
            if len(value) > maximum or any(ord(char) < 32 for char in value):
                raise ResearchActionError("filters_json is invalid")
    return str(view_key), parsed


def _payload_hash(value: Mapping[str, object]) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _existing_or_claim(cur, *, actor: str, action: str, key: str, digest: str):
    cur.execute(
        """
        insert into research_user_action(
          idempotency_key, actor, action_type, payload_hash
        ) values (%s,%s,%s,%s)
        on conflict do nothing
        returning idempotency_key
        """,
        (key, actor, action, digest),
    )
    if cur.fetchone() is not None:
        return None
    cur.execute(
        """
        select actor, action_type, payload_hash, outcome
        from research_user_action
        where idempotency_key=%s
        """,
        (key,),
    )
    row = cur.fetchone()
    if (
        row is None
        or row["actor"] != actor
        or row["action_type"] != action
        or row["payload_hash"] != digest
    ):
        raise ResearchActionConflict("RESEARCH_ACTION_IDEMPOTENCY_CONFLICT")
    if not isinstance(row["outcome"], Mapping):
        raise ResearchActionConflict("RESEARCH_ACTION_RECONCILIATION_REQUIRED")
    replay = dict(row["outcome"])
    replay["idempotent_replay"] = True
    replay["db_writes"] = 0
    return replay


def _finish(cur, key: str, outcome: dict[str, object]) -> dict[str, object]:
    stored = dict(outcome)
    stored.pop("idempotent_replay", None)
    stored.pop("db_writes", None)
    cur.execute(
        "update research_user_action set outcome=%s where idempotency_key=%s",
        (Jsonb(stored), key),
    )
    return outcome


def _assert_assets(cur, asset_type: str, ids: list[str]) -> tuple[str, str]:
    if asset_type not in _ASSETS:
        raise ResearchActionError("asset_type is invalid")
    table, column = _ASSETS[asset_type]
    cur.execute(
        f"select count(*)::int as asset_count from {table} where id=any(%s::uuid[])",
        (ids,),
    )
    if cur.fetchone()["asset_count"] != len(ids):
        raise ResearchActionError("one or more assets do not exist")
    return table, column


def _mutate(
    db: postgresql,
    *,
    actor: str,
    action: str,
    idempotency_key: str,
    asset_type: str,
    asset_ids: list[str],
    monitoring_status: str,
    monitoring_priority: float | None,
    collection_name: str,
    note: str,
    view_key: str,
    filter_name: str,
    filters_json: str,
) -> dict[str, object]:
    key = _uuid4(idempotency_key, field="idempotency_key")
    payload: dict[str, object] = {"action": action}
    normalized_ids: list[str] = []
    if action in {"set_monitoring", "add_to_collection", "remove_from_collection"}:
        normalized_ids = _asset_ids(asset_ids)
        if asset_type not in _ASSETS:
            raise ResearchActionError("asset_type is invalid")
        payload.update(asset_type=asset_type, asset_ids=normalized_ids)
    if action == "set_monitoring":
        if monitoring_status not in _MONITORING_STATUSES:
            raise ResearchActionError("monitoring_status is invalid")
        if monitoring_priority is not None:
            monitoring_priority = float(monitoring_priority)
            if not 0 <= monitoring_priority <= 100:
                raise ResearchActionError("monitoring_priority is invalid")
        payload.update(status=monitoring_status, priority=monitoring_priority)
    elif action in {"add_to_collection", "remove_from_collection"}:
        collection_name = _text(collection_name, field="collection_name", maximum=80)
        note = _text(note, field="note", maximum=500, required=False)
        payload.update(collection_name=collection_name, note=note)
    elif action == "save_filter":
        view_key, parsed_filters = _filters(view_key, filters_json)
        filter_name = _text(filter_name, field="filter_name", maximum=80)
        payload.update(view_key=view_key, filter_name=filter_name, filters=parsed_filters)
    else:
        raise ResearchActionError("action is invalid")

    digest = _payload_hash(payload)
    with psycopg.connect(_dsn(db), row_factory=dict_row) as conn, conn.cursor() as cur:
        replay = _existing_or_claim(
            cur, actor=actor, action=action, key=key, digest=digest
        )
        if replay is not None:
            return replay

        if action == "set_monitoring":
            table, _ = _assert_assets(cur, asset_type, normalized_ids)
            cur.execute(
                f"""
                update {table}
                set monitoring_status=%s,
                    monitoring_priority=coalesce(%s, monitoring_priority),
                    next_due_at=case
                      when %s in ('monitoring','follow_up','selected') then now()
                      else null
                    end
                where id=any(%s::uuid[])
                returning id
                """,
                (monitoring_status, monitoring_priority, monitoring_status, normalized_ids),
            )
            changed = len(cur.fetchall())
        elif action in {"add_to_collection", "remove_from_collection"}:
            _, target_column = _assert_assets(cur, asset_type, normalized_ids)
            if action == "add_to_collection":
                cur.execute(
                    """
                    insert into collection(name, description, created_by)
                    values (%s, '研究台用户专题', %s)
                    on conflict do nothing
                    """,
                    (collection_name, actor),
                )
            cur.execute(
                """
                select id from collection
                where created_by=%s and lower(name)=lower(%s)
                order by created_at, id
                limit 1
                """,
                (actor, collection_name),
            )
            collection_row = cur.fetchone()
            if action == "add_to_collection":
                collection_id = collection_row["id"]
                table = _ASSETS[asset_type][0]
                cur.execute(
                    f"""
                    insert into collection_item(collection_id, {target_column}, note)
                    select %s, id, %s from {table} where id=any(%s::uuid[])
                    on conflict do nothing
                    returning {target_column}
                    """,
                    (collection_id, note or None, normalized_ids),
                )
                changed = len(cur.fetchall())
            elif collection_row is None:
                changed = 0
            else:
                collection_id = collection_row["id"]
                cur.execute(
                    f"""
                    delete from collection_item
                    where collection_id=%s and {target_column}=any(%s::uuid[])
                    returning {target_column}
                    """,
                    (collection_id, normalized_ids),
                )
                changed = len(cur.fetchall())
        else:
            parsed_filters = payload["filters"]
            cur.execute(
                """
                insert into saved_research_filter(actor, view_key, name, filters)
                values (%s,%s,%s,%s)
                on conflict(actor, view_key, name) do update
                set filters=excluded.filters, updated_at=now()
                """,
                (actor, view_key, filter_name, Jsonb(parsed_filters)),
            )
            changed = 1

        outcome = {
            "status": "saved",
            "action": action,
            "changed_count": changed,
            "idempotent_replay": False,
            "db_writes": changed,
            "external_calls": 0,
            "llm_calls": 0,
        }
        return _finish(cur, key, outcome)


def main(
    db: postgresql,
    action: str,
    idempotency_key: str,
    asset_type: str = "",
    asset_ids: list[str] | None = None,
    monitoring_status: str = "",
    monitoring_priority: float | None = None,
    collection_name: str = "",
    note: str = "",
    view_key: str = "",
    filter_name: str = "",
    filters_json: str = "{}",
    writer_allowlist: str = "",
):
    try:
        return _mutate(
            db,
            actor=_authorized_writer(writer_allowlist),
            action=action,
            idempotency_key=idempotency_key,
            asset_type=asset_type,
            asset_ids=asset_ids or [],
            monitoring_status=monitoring_status,
            monitoring_priority=monitoring_priority,
            collection_name=collection_name,
            note=note,
            view_key=view_key,
            filter_name=filter_name,
            filters_json=filters_json,
        )
    except (PermissionError, ResearchActionConflict, ResearchActionError):
        raise
    except Exception:
        raise RuntimeError("RESEARCH_ACTION_FAILED") from None
