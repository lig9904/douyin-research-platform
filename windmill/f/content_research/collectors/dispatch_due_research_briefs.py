# /// script
# requires-python = "==3.13.*"
# dependencies = [
#   "psycopg[binary]==3.3.6",
#   "wmill==1.815.0",
# ]
# ///

"""Dispatch a small batch of due briefs; performs no provider calls itself."""

from __future__ import annotations

from typing import TypedDict

import psycopg
from psycopg.rows import dict_row


DATABASE_PATH = "f/content_research/research_db"
FLOW_PATH = "f/content_research/flows/research_brief_cycle"


class postgresql(TypedDict):
    host: str
    port: int
    user: str
    password: str
    dbname: str
    sslmode: str


def _resource() -> postgresql:
    import wmill

    return wmill.get_resource(DATABASE_PATH)


def _due(db: postgresql) -> list[str]:
    args = {
        "host": db["host"], "port": int(db.get("port", 5432)),
        "user": db["user"], "password": db["password"],
        "dbname": db["dbname"], "sslmode": db.get("sslmode", "prefer"),
    }
    with psycopg.connect(**args, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("set transaction read only")
        cur.execute(
            """
            select id from research_brief
            where status='active' and next_due_at <= now()
            order by next_due_at, id
            limit 5
            """
        )
        return [str(row["id"]) for row in cur.fetchall()]


def _safe_failure(stage: str, exc: Exception) -> RuntimeError:
    """Expose an actionable category without returning credentials or SQL text."""
    sqlstate = getattr(exc, "sqlstate", None)
    category = type(exc).__name__
    suffix = f":{sqlstate}" if isinstance(sqlstate, str) and sqlstate else ""
    return RuntimeError(f"research brief dispatch {stage} failed [{category}{suffix}]")


def main() -> dict[str, object]:
    try:
        db = _resource()
    except Exception as exc:
        raise _safe_failure("resource", exc) from None
    try:
        due = _due(db)
    except Exception as exc:
        raise _safe_failure("query", exc) from None
    import wmill

    jobs = [
        wmill.run_flow_async(
            path=FLOW_PATH,
            args={"brief_id": brief_id},
            do_not_track_in_parent=False,
        )
        for brief_id in due
    ]
    return {
        "status": "dispatched",
        "due_count": len(due),
        "dispatched_count": len(jobs),
        "job_ids": [str(job_id) for job_id in jobs],
        "external_calls": 0,
        "llm_calls": 0,
    }
