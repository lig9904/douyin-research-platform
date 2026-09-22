# /// script
# requires-python = "==3.13.*"
# dependencies = [
#   "douyin-research-platform @ git+https://github.com/lig9904/douyin-research-platform@a46b12647ebeb4a133536bf97c8578161c89bed7",
#   "psycopg[binary]==3.3.6",
#   "wmill==1.815.0",
# ]
# ///

"""Claim and execute one due research brief with fixed service credentials."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator, Mapping, TypedDict
from uuid import UUID

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from douyin_research.l0l1.research_briefs import make_config, run_live


DATABASE_PATH = "f/content_research/research_db"
IDENTITY_PATH = "f/content_research/automation_worker_identity"
API_KEY_PATH = "f/content_research/tikhub_api_key"
LOCK_NAME = "douyin_research:manual_golden_intake"


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


def _variable(path: str) -> str:
    import wmill

    return wmill.get_variable(path)


def _dsn(db: postgresql) -> str:
    return make_conninfo(
        host=db["host"], port=int(db.get("port", 5432)), user=db["user"],
        password=db["password"], dbname=db["dbname"],
        sslmode=db.get("sslmode", "prefer"),
    )


def _brief_id(value: object) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise ValueError("brief_id is invalid") from None


def _identity(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("worker identity is invalid")
    normalized = value.strip()
    if not normalized or len(normalized) > 160 or not normalized.isprintable():
        raise ValueError("worker identity is invalid")
    return normalized


@contextmanager
def _single_paid_job(dsn: str) -> Iterator[bool]:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("select pg_try_advisory_lock(hashtext(%s))", (LOCK_NAME,))
        acquired = bool(cur.fetchone()[0])
        if not acquired:
            yield False
            return
        try:
            yield True
        finally:
            cur.execute("select pg_advisory_unlock(hashtext(%s))", (LOCK_NAME,))


def _claim(dsn: str, brief_id: UUID, actor: str) -> dict[str, Any] | None:
    with psycopg.connect(dsn, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute(
            "select pg_advisory_xact_lock(hashtext(%s))",
            (f"research_brief:{brief_id}",),
        )
        cur.execute(
            """
            select id, platform, source_type, target, time_window_hours,
              max_items, depth, cadence_hours, config_version, next_due_at
            from research_brief
            where id=%s and status='active' and next_due_at <= now()
            for update
            """,
            (brief_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        snapshot = {
            "platform": row["platform"],
            "source_type": row["source_type"],
            "target": row["target"],
            "time_window_hours": row["time_window_hours"],
            "max_items": row["max_items"],
            "depth": row["depth"],
            "cadence_hours": row["cadence_hours"],
        }
        due_at: datetime = row["next_due_at"]
        dispatch_key = f"{brief_id}:{row['config_version']}:{due_at.isoformat()}"
        cur.execute(
            """
            insert into research_brief_run(
              brief_id, brief_version, dispatch_key, trigger_kind,
              triggered_by, status, config_snapshot
            ) values (%s,%s,%s,'schedule',%s,'running',%s)
            on conflict(dispatch_key) do nothing returning id
            """,
            (
                brief_id, row["config_version"], dispatch_key, actor,
                Jsonb(snapshot),
            ),
        )
        run_row = cur.fetchone()
        if run_row is None:
            return None
        if row["cadence_hours"] is None:
            cur.execute(
                """
                update research_brief set status='paused', next_due_at=null,
                  last_dispatched_at=now(), updated_at=now()
                where id=%s
                """,
                (brief_id,),
            )
        else:
            cur.execute(
                """
                update research_brief set
                  next_due_at=now() + make_interval(hours => cadence_hours),
                  last_dispatched_at=now(), updated_at=now()
                where id=%s
                """,
                (brief_id,),
            )
        return {"brief_run_id": run_row["id"], "config": snapshot}


def _finish(
    dsn: str, brief_run_id: UUID, *, status: str,
    source_run_id: UUID | None = None, summary: Mapping[str, Any] | None = None,
    error_code: str | None = None,
) -> None:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            update research_brief_run set status=%s, source_run_id=%s,
              summary=%s, error_code=%s, finished_at=now()
            where id=%s and status='running'
            """,
            (status, source_run_id, Jsonb(dict(summary or {})), error_code, brief_run_id),
        )


def _safe_result(result: Mapping[str, Any], brief_run_id: UUID) -> dict[str, Any]:
    integer_fields = (
        "observations", "unique_platform_videos", "new_candidate_count",
        "scored_videos", "provider_call_count", "cached_call_count",
        "uncached_call_count", "max_external_calls", "sdk_retries",
    )
    values = {name: result.get(name) for name in integer_fields}
    if any(type(value) is not int or value < 0 for value in values.values()):
        raise RuntimeError("research brief returned an invalid summary")
    if values["provider_call_count"] != (
        values["cached_call_count"] + values["uncached_call_count"]
    ):
        raise RuntimeError("research brief returned an invalid summary")
    flags = {
        name: result.get(name)
        for name in (
            "collect_comments", "collect_media", "review_required",
            "auto_submit_asr", "auto_submit_l3",
        )
    }
    if any(type(value) is not bool for value in flags.values()):
        raise RuntimeError("research brief returned an invalid summary")
    if flags["auto_submit_asr"] or flags["auto_submit_l3"]:
        raise RuntimeError("research brief analysis boundary is invalid")
    return {
        "status": "completed",
        "brief_run_id": str(brief_run_id),
        "run_id": str(UUID(str(result["run_id"]))),
        **values,
        **flags,
        "external_calls": values["uncached_call_count"],
        "raw_provider_payload_included": False,
    }


def main(brief_id: str) -> dict[str, Any]:
    normalized_id = _brief_id(brief_id)
    try:
        dsn = _dsn(_resource())
        actor = _identity(_variable(IDENTITY_PATH))
    except Exception:
        raise RuntimeError("research brief configuration is unavailable") from None

    with _single_paid_job(dsn) as acquired:
        if not acquired:
            return {
                "status": "deferred", "external_calls": 0, "sdk_retries": 0,
                "collect_comments": False, "collect_media": False,
            }
        claim = _claim(dsn, normalized_id, actor)
        if claim is None:
            return {
                "status": "deferred", "external_calls": 0, "sdk_retries": 0,
                "collect_comments": False, "collect_media": False,
            }
        brief_run_id: UUID = claim["brief_run_id"]
        try:
            config = make_config(**claim["config"])
            api_key = (_variable(API_KEY_PATH) or "").strip()
            if not api_key:
                raise RuntimeError("TikHub secret is not configured")
            result = run_live(dsn=dsn, api_key=api_key, config=config, triggered_by=actor)
            safe = _safe_result(result, brief_run_id)
            source_run_id = UUID(safe["run_id"])
            _finish(
                dsn, brief_run_id, status="success", source_run_id=source_run_id,
                summary=safe,
            )
            return safe
        except Exception:
            _finish(
                dsn, brief_run_id, status="failed",
                error_code="RESEARCH_BRIEF_EXECUTION_FAILED",
                summary={
                    "external_calls": None,
                    "call_count_status": "use_external_api_call_ledger",
                    "raw_provider_payload_included": False,
                },
            )
            raise RuntimeError("research brief execution failed") from None
