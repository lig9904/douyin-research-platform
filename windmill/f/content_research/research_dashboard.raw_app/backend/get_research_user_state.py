#requirements:
#psycopg[binary]==3.3.6

"""Return only the current actor's saved collections and filters."""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any, TypedDict

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


def _json(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json(item) for item in value]
    return value


def main(db: postgresql, view_key: str = "videos"):
    actor = _actor()
    if view_key not in {"videos", "accounts", "hotspots"}:
        raise ValueError("view_key is invalid")
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
            cur.execute("set transaction read only")
            cur.execute(
                """
                select c.name,
                  count(ci.video_id)::int as video_count,
                  count(ci.account_id)::int as account_count,
                  count(ci.signal_id)::int as signal_count
                from collection c
                left join collection_item ci on ci.collection_id=c.id
                where c.created_by=%s
                group by c.id, c.name
                order by lower(c.name), c.id
                limit 50
                """,
                (actor,),
            )
            collections = [_json(dict(row)) for row in cur.fetchall()]
            cur.execute(
                """
                select name, filters, updated_at
                from saved_research_filter
                where actor=%s and view_key=%s
                order by updated_at desc, name
                limit 50
                """,
                (actor, view_key),
            )
            filters = [_json(dict(row)) for row in cur.fetchall()]
        return {
            "collections": collections,
            "saved_filters": filters,
            "view_key": view_key,
            "read_only": True,
        }
    except PermissionError:
        raise
    except Exception:
        raise RuntimeError("RESEARCH_USER_STATE_UNAVAILABLE") from None
