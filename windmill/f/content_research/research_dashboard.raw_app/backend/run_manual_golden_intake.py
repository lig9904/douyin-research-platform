#requirements:
#douyin-research-platform @ git+https://github.com/lig9904/douyin-research-platform@370c09880ae1ae6332143834e165a45a0dfe8284
#psycopg[binary]==3.3.6
#wmill==1.815.0

"""Authenticated app entrypoint for the bounded TikHub golden intake."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator, TypedDict

import psycopg
from psycopg.conninfo import make_conninfo

from douyin_research.l0l1.real_data import make_plan, plan_dict, run_live
from douyin_research.l3 import authorize_reviewer


CONFIRMATION = "RUN_TIKHUB_GOLDEN_PAID"
MAX_COST_USD = 0.01
LOCK_NAME = "douyin_research:manual_golden_intake"
API_KEY_PATH = "f/content_research/tikhub_api_key"
WRITER_ALLOWLIST_PATH = "f/content_research/research_action_writers"


class postgresql(TypedDict):
    host: str
    port: int
    user: str
    password: str
    dbname: str
    sslmode: str


def _dsn(db: postgresql) -> str:
    return make_conninfo(
        host=db["host"],
        port=int(db.get("port", 5432)),
        user=db["user"],
        password=db["password"],
        dbname=db["dbname"],
        sslmode=db.get("sslmode", "prefer"),
    )


def _preflight(dsn: str) -> None:
    required = (
        "source_video",
        "metric_snapshot",
        "pipeline_run",
        "daily_budget",
        "external_api_call",
    )
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        for table in required:
            cur.execute("select to_regclass(%s)", (table,))
            if cur.fetchone()[0] is None:
                raise RuntimeError("research schema is not ready")


def _windmill_variable(path: str) -> str:
    import wmill

    return wmill.get_variable(path)


@contextmanager
def _single_paid_job(dsn: str) -> Iterator[None]:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("select pg_try_advisory_lock(hashtext(%s))", (LOCK_NAME,))
        if not cur.fetchone()[0]:
            raise RuntimeError("another manual golden intake is already running")
        try:
            yield
        finally:
            cur.execute("select pg_advisory_unlock(hashtext(%s))", (LOCK_NAME,))


def _safe_result(result: dict[str, Any], max_cost_usd: float) -> dict[str, Any]:
    return {
        "status": "completed",
        "execute": True,
        "source_count": int(result["source_count"]),
        "observations": int(result["observations"]),
        "unique_platform_videos": int(result["unique_platform_videos"]),
        "scored_videos": int(result["scored_videos"]),
        "max_external_calls": int(result["max_external_calls"]),
        "provider_call_count": int(result["provider_call_count"]),
        "cached_call_count": int(result["cached_call_count"]),
        "uncached_call_count": int(result["uncached_call_count"]),
        "retry_count": int(result["retry_count"]),
        "maximum_cost_usd": max_cost_usd,
        "raw_provider_payload_included": False,
    }


def main(
    db: postgresql,
    execute: bool = False,
    confirmation: str = "",
    max_items: int = 1,
    max_external_calls: int = 1,
    max_cost_usd: float = MAX_COST_USD,
    date_window_hours: int = 24,
    enrich_details: bool = False,
    force_refresh: bool = True,
):
    if max_cost_usd > MAX_COST_USD:
        raise ValueError(f"max_cost_usd cannot exceed {MAX_COST_USD}")
    plan = make_plan(
        dry_run=not execute,
        max_items=max_items,
        max_external_calls=max_external_calls,
        max_cost_usd=max_cost_usd,
        date_window_hours=date_window_hours,
        enrich_details=enrich_details,
        force_refresh=force_refresh,
    )
    if not execute:
        return {**plan_dict(plan), "status": "preview", "external_calls": 0}
    if confirmation != CONFIRMATION:
        raise PermissionError("exact paid-operation confirmation is required")

    dsn = _dsn(db)
    with _single_paid_job(dsn):
        _preflight(dsn)
        actor = authorize_reviewer(
            os.environ.get("WM_END_USER_EMAIL"),
            _windmill_variable(WRITER_ALLOWLIST_PATH),
        )
        api_key = (_windmill_variable(API_KEY_PATH) or "").strip()
        if not api_key:
            raise RuntimeError("TikHub secret is not configured")
        try:
            result = run_live(
                dsn=dsn,
                api_key=api_key,
                plan=plan,
                triggered_by=actor,
            )
        except Exception:
            # Do not expose request IDs, provider responses, content, or secrets.
            raise RuntimeError("TikHub golden intake failed") from None
    return _safe_result(result, plan.max_cost_usd)
