# /// script
# requires-python = "==3.14.*"
# dependencies = [
#   "douyin-research-platform @ git+https://github.com/lig9904/douyin-research-platform@e32581b9f771ea75a4d53f579df1f83dac60521a",
#   "psycopg[binary]==3.3.6",
#   "wmill==1.815.0",
# ]
# ///

"""Unattended, bounded TikHub golden intake for the Windmill scheduler.

This is intentionally a separate service identity entrypoint.  It never
accepts caller-provided values and does not change the manual intake's
end-user review/confirmation boundary.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Iterator, Mapping, TypedDict
from uuid import UUID

import psycopg
from psycopg.conninfo import make_conninfo

from douyin_research.l0l1.real_data import make_plan, run_live


DATABASE_PATH = "f/content_research/research_db"
IDENTITY_PATH = "f/content_research/automation_worker_identity"
SETTINGS_PATH = "f/content_research/scheduled_golden_settings"
API_KEY_PATH = "f/content_research/tikhub_api_key"
LOCK_NAME = "douyin_research:manual_golden_intake"


class postgresql(TypedDict):
    host: str
    port: int
    user: str
    password: str
    dbname: str
    sslmode: str


def _windmill_variable(path: str) -> str:
    import wmill

    return wmill.get_variable(path)


def _database_resource() -> postgresql:
    import wmill

    return wmill.get_resource(DATABASE_PATH)


def _dsn(db: postgresql) -> str:
    return make_conninfo(
        host=db["host"],
        port=int(db.get("port", 5432)),
        user=db["user"],
        password=db["password"],
        dbname=db["dbname"],
        sslmode=db.get("sslmode", "prefer"),
    )


def _worker_identity(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("scheduled worker identity is invalid")
    identity = value.strip()
    if (
        not identity
        or len(identity) > 160
        or any(not character.isprintable() for character in identity)
    ):
        raise ValueError("scheduled worker identity is invalid")
    return identity


def _scheduled_plan(raw: object):
    """Parse the sole scheduler-owned JSON settings before any secret lookup."""
    if not isinstance(raw, str):
        raise ValueError("scheduled golden settings are invalid")
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        raise ValueError("scheduled golden settings are invalid") from None
    if not isinstance(data, dict) or set(data) != {"max_items", "date_window_hours"}:
        raise ValueError("scheduled golden settings are invalid")
    max_items = data["max_items"]
    date_window_hours = data["date_window_hours"]
    if type(max_items) is not int or type(date_window_hours) is not int:
        raise ValueError("scheduled golden settings are invalid")
    return make_plan(
        dry_run=False,
        max_items=max_items,
        date_window_hours=date_window_hours,
        enrich_details=True,
        force_refresh=False,
        max_external_calls=2,
        max_cost_usd=None,
        detail_strategy="batch50",
        novel_candidates_only=True,
    )


@contextmanager
def _single_paid_job(dsn: str) -> Iterator[bool]:
    """Share the manual lock; contention is a safe, non-error deferral."""
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


def _completed_run(dsn: str, run_id: UUID) -> None:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "select status, run_type, platform from pipeline_run where id=%s",
            (run_id,),
        )
        row = cur.fetchone()
    if row != ("success", "l0l1_discovery", "douyin"):
        raise RuntimeError("scheduled golden intake did not complete successfully")


def _safe_summary(result: Mapping[str, Any], run_id: UUID) -> dict[str, object]:
    required_counts = (
        "source_count",
        "observations",
        "unique_platform_videos",
        "new_candidate_count",
        "scored_videos",
        "provider_call_count",
        "cached_call_count",
        "uncached_call_count",
    )
    try:
        counts = {name: result[name] for name in required_counts}
    except KeyError:
        raise RuntimeError("scheduled golden intake returned an invalid summary") from None
    if any(type(value) is not int or value < 0 for value in counts.values()):
        raise RuntimeError("scheduled golden intake returned an invalid summary")
    if counts["provider_call_count"] != (
        counts["cached_call_count"] + counts["uncached_call_count"]
    ):
        raise RuntimeError("scheduled golden intake returned an invalid summary")
    if (
        counts["new_candidate_count"] > counts["unique_platform_videos"]
        or counts["scored_videos"] > counts["unique_platform_videos"]
    ):
        raise RuntimeError("scheduled golden intake returned an invalid summary")
    return {
        "status": "completed",
        "run_id": str(run_id),
        **counts,
        "external_calls": counts["uncached_call_count"],
        "sdk_retries": 0,
        "raw_provider_payload_included": False,
    }


def main() -> dict[str, object]:
    """Run one scheduler-owned golden batch, returning only aggregate metadata."""
    try:
        dsn = _dsn(_database_resource())
        actor = _worker_identity(_windmill_variable(IDENTITY_PATH))
        plan = _scheduled_plan(_windmill_variable(SETTINGS_PATH))
    except Exception:
        raise RuntimeError("scheduled golden intake configuration is unavailable") from None

    try:
        with _single_paid_job(dsn) as acquired:
            if not acquired:
                return {"status": "deferred", "external_calls": 0, "sdk_retries": 0}

            # This is deliberately the final Windmill lookup before the paid call.
            api_key = (_windmill_variable(API_KEY_PATH) or "").strip()
            if not api_key:
                raise RuntimeError("TikHub secret is not configured")
            try:
                result = run_live(
                    dsn=dsn, api_key=api_key, plan=plan, triggered_by=actor
                )
            except Exception:
                raise RuntimeError("TikHub scheduled golden intake failed") from None
            try:
                run_id = UUID(str(result["run_id"]))
            except (KeyError, TypeError, ValueError, AttributeError):
                raise RuntimeError("scheduled golden intake returned an invalid run ID") from None
            _completed_run(dsn, run_id)
            return _safe_summary(result, run_id)
    except Exception:
        # Never expose upstream response text, request URLs, or credentials in job logs.
        raise RuntimeError("TikHub scheduled golden intake failed") from None
