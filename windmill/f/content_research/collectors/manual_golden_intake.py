#requirements:
#douyin-research-platform @ git+https://github.com/lig9904/douyin-research-platform@c45d988cea43e8c5a28de64563af19cea4a46e37
#psycopg[binary]==3.3.6
#wmill==1.815.0

"""Manual, bounded TikHub-to-L0/L1 golden intake for Windmill."""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator, TypedDict

import psycopg
from psycopg.conninfo import make_conninfo

from douyin_research.l0l1.real_data import GoldenIntakePlan, make_plan, plan_dict, run_live
from douyin_research.l3 import authorize_reviewer


CONFIRMATION = "RUN_TIKHUB_GOLDEN_PAID"
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


@dataclass(frozen=True, slots=True)
class ManualGoldenRequest:
    execute: bool = False
    confirmation: str = ""
    max_items: int = 5
    max_external_calls: int = 2
    max_cost_usd: float | None = None
    date_window_hours: int = 24
    enrich_details: bool = True
    force_refresh: bool = False


def _plan(request: ManualGoldenRequest) -> GoldenIntakePlan:
    return make_plan(
        dry_run=not request.execute,
        max_items=request.max_items,
        max_external_calls=request.max_external_calls,
        max_cost_usd=request.max_cost_usd,
        date_window_hours=request.date_window_hours,
        enrich_details=request.enrich_details,
        force_refresh=request.force_refresh,
    )


def _execute(
    request: ManualGoldenRequest,
    *,
    preflight: Callable[[], None],
    authorize: Callable[[], str],
    load_api_key: Callable[[], str],
    collect: Callable[[str, str, GoldenIntakePlan], dict[str, Any]],
) -> dict[str, Any]:
    plan = _plan(request)
    if not request.execute:
        preview = plan_dict(plan)
        preview.update({"status": "preview", "external_calls": 0})
        return preview
    if request.confirmation != CONFIRMATION:
        raise PermissionError("exact paid-operation confirmation is required")

    # Host/database and actor checks happen before the provider secret is read.
    preflight()
    actor = authorize()
    api_key = (load_api_key() or "").strip()
    if not api_key:
        raise RuntimeError("TikHub secret is not configured")
    result = collect(api_key, actor, plan)
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
        "maximum_cost_usd": plan.max_cost_usd,
        "raw_provider_payload_included": False,
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


def _preflight(dsn: str) -> None:
    required = ("source_video", "metric_snapshot", "pipeline_run", "daily_budget", "external_api_call")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("select to_regclass(%s)", (required[0],))
        if cur.fetchone()[0] is None:
            raise RuntimeError("research schema is not ready")
        for table in required[1:]:
            cur.execute("select to_regclass(%s)", (table,))
            if cur.fetchone()[0] is None:
                raise RuntimeError("research schema is not ready")


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


def _windmill_variable(path: str) -> str:
    import wmill

    return wmill.get_variable(path)


def _authorize_writer() -> str:
    allowlist = _windmill_variable(WRITER_ALLOWLIST_PATH)
    return authorize_reviewer(os.environ.get("WM_END_USER_EMAIL"), allowlist)


def _collect(dsn: str, api_key: str, actor: str, plan: GoldenIntakePlan) -> dict[str, Any]:
    try:
        return run_live(
            dsn=dsn,
            api_key=api_key,
            plan=plan,
            triggered_by=actor,
        )
    except Exception:
        # Windmill logs must not inherit an upstream response, request id, or key.
        raise RuntimeError("TikHub golden intake failed") from None


def main(
    db: postgresql,
    execute: bool = False,
    confirmation: str = "",
    max_items: int = 5,
    max_external_calls: int = 2,
    max_cost_usd: float | None = None,
    date_window_hours: int = 24,
    enrich_details: bool = True,
    force_refresh: bool = False,
):
    request = ManualGoldenRequest(
        execute=execute,
        confirmation=confirmation,
        max_items=max_items,
        max_external_calls=max_external_calls,
        max_cost_usd=max_cost_usd,
        date_window_hours=date_window_hours,
        enrich_details=enrich_details,
        force_refresh=force_refresh,
    )
    dsn = _dsn(db)
    if not execute:
        return _execute(
            request,
            preflight=lambda: None,
            authorize=lambda: "",
            load_api_key=lambda: "",
            collect=lambda _key, _actor, _plan: {},
        )

    with _single_paid_job(dsn):
        return _execute(
            request,
            preflight=lambda: _preflight(dsn),
            authorize=_authorize_writer,
            load_api_key=lambda: _windmill_variable(API_KEY_PATH),
            collect=lambda key, actor, plan: _collect(dsn, key, actor, plan),
        )
