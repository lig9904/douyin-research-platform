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


def main(db: postgresql):
    actor = _actor()
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
                select id, name, platform, source_type, target,
                  time_window_hours, max_items, depth, cadence_hours,
                  status, config_version, next_due_at, last_dispatched_at,
                  created_at, updated_at
                from research_brief
                where owner_actor=%s and status <> 'archived'
                order by created_at desc, id
                limit 100
                """,
                (actor,),
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
                    where b.owner_actor=%s and r.brief_id=any(%s::uuid[])
                    order by r.started_at desc, r.id
                    limit 200
                    """,
                    (actor, brief_ids),
                )
                runs = [_json(dict(row)) for row in cur.fetchall()]
        return {"briefs": briefs, "runs": runs, "read_only": True}
    except PermissionError:
        raise
    except Exception:
        raise RuntimeError("RESEARCH_BRIEFS_UNAVAILABLE") from None
