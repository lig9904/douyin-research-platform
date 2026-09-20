# /// script
# requires-python = "==3.12.*"
# dependencies = [
#   "douyin-research-platform @ git+https://github.com/lig9904/douyin-research-platform@5b7341ed0f4d86c6959a9045cad50ea6c1bb3b74",
#   "psycopg[binary]==3.3.6",
# ]
# ///

"""Run the bounded intake in the authenticated app job itself."""

from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib import parse, request


CONFIRMATION = "RUN_TIKHUB_GOLDEN_PAID"
DB_RESOURCE = "$res:f/content_research/research_db"
WRITER_ALLOWLIST_PATH = "f/content_research/research_action_writers"
API_KEY_PATH = "f/content_research/tikhub_api_key"
LOCK_NAME = "douyin_research:manual_golden_intake"


def _api(method: str, endpoint: str, payload: dict[str, Any] | None = None) -> Any:
    base = (os.environ.get("BASE_INTERNAL_URL") or os.environ.get("WM_BASE_URL") or "").rstrip("/")
    token = os.environ.get("WM_TOKEN", "")
    if not base or not token:
        raise RuntimeError("Windmill runtime context is unavailable")
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = request.Request(
        f"{base}/api/{endpoint.lstrip('/')}",
        data=body,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=15) as response:
            raw = response.read().decode("utf-8")
    except Exception:
        raise RuntimeError("Windmill internal dispatch failed") from None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _workspace() -> str:
    value = os.environ.get("WM_WORKSPACE", "").strip()
    if not value:
        raise RuntimeError("Windmill workspace context is unavailable")
    return parse.quote(value, safe="")


def _get_variable(path: str) -> str:
    encoded = parse.quote(path, safe="/")
    value = _api("GET", f"w/{_workspace()}/variables/get_value/{encoded}")
    return value if isinstance(value, str) else ""


def _run_intake(args: dict[str, Any], actor: str) -> dict[str, Any]:
    import psycopg
    from psycopg.conninfo import make_conninfo
    from douyin_research.l0l1.real_data import make_plan, run_live

    plan = make_plan(dry_run=False, **{
        key: value for key, value in args.items()
        if key not in {"execute", "confirmation"}
    })
    path = parse.quote(DB_RESOURCE.removeprefix("$res:"), safe="/")
    db = _api("GET", f"w/{_workspace()}/resources/get_value_interpolated/{path}")
    dsn = make_conninfo(**{key: db[key] for key in
        ("host", "port", "user", "password", "dbname", "sslmode") if key in db})
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("select pg_try_advisory_lock(hashtext(%s))", (LOCK_NAME,))
        if not cur.fetchone()[0]:
            raise RuntimeError("another manual golden intake is already running")
        try:
            api_key = _get_variable(API_KEY_PATH).strip()
            if not api_key:
                raise RuntimeError("TikHub secret is not configured")
            result = run_live(dsn=dsn, api_key=api_key, plan=plan, triggered_by=actor)
        finally:
            cur.execute("select pg_advisory_unlock(hashtext(%s))", (LOCK_NAME,))
    fields = ("source_count", "observations", "unique_platform_videos", "scored_videos",
              "max_external_calls", "provider_call_count", "cached_call_count",
              "uncached_call_count", "retry_count")
    return {"status": "completed", "execute": True,
            **{key: int(result[key]) for key in fields},
            "maximum_cost_usd": plan.max_cost_usd, "raw_provider_payload_included": False}


def _authorized_viewer(end_user_email: str | None, allowlist: str | None) -> str:
    if not isinstance(end_user_email, str) or not end_user_email:
        raise PermissionError("authenticated end-user email is required")
    if end_user_email != end_user_email.strip() or end_user_email != end_user_email.lower():
        raise PermissionError("authenticated end-user email must be lowercase")
    if not isinstance(allowlist, str) or not allowlist.strip():
        raise PermissionError("writer allowlist is required")
    source = allowlist.strip()
    if source.startswith("["):
        try:
            values = json.loads(source)
        except json.JSONDecodeError:
            raise PermissionError("writer allowlist is invalid") from None
        if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
            raise PermissionError("writer allowlist is invalid")
    else:
        values = re.split(r"[,\n]", source)
    allowed = {value.strip() for value in values}
    if not allowed or any(not value or value != value.lower() for value in allowed):
        raise PermissionError("writer allowlist is invalid")
    if end_user_email not in allowed:
        raise PermissionError("authenticated end-user is not an allowed writer")
    return end_user_email


def _plan(
    *,
    execute: bool,
    confirmation: str,
    max_items: int,
    max_external_calls: int,
    max_cost_usd: float | None,
    date_window_hours: int,
    enrich_details: bool,
    force_refresh: bool,
) -> dict[str, Any]:
    if not 1 <= int(max_items) <= 5:
        raise ValueError("max_items must be between 1 and 5")
    if not 1 <= int(max_external_calls) <= 2:
        raise ValueError("max_external_calls must be between 1 and 2")
    if max_cost_usd is not None and float(max_cost_usd) < 0:
        raise ValueError("max_cost_usd must be non-negative or None")
    if not 1 <= int(date_window_hours) <= 24:
        raise ValueError("date_window_hours must be between 1 and 24")
    if int(max_external_calls) < 1 + int(bool(enrich_details)):
        raise ValueError("max_external_calls is too small for detail enrichment")
    if execute and confirmation != CONFIRMATION:
        raise PermissionError("exact paid-operation confirmation is required")
    return {
        "execute": bool(execute),
        "confirmation": confirmation,
        "max_items": int(max_items),
        "max_external_calls": int(max_external_calls),
        "max_cost_usd": None if max_cost_usd is None else float(max_cost_usd),
        "date_window_hours": int(date_window_hours),
        "enrich_details": bool(enrich_details),
        "force_refresh": bool(force_refresh),
    }


def main(
    execute: bool = False,
    confirmation: str = "",
    max_items: int = 5,
    max_external_calls: int = 2,
    max_cost_usd: float | None = None,
    date_window_hours: int = 24,
    enrich_details: bool = True,
    force_refresh: bool = True,
):
    request = _plan(
        execute=execute,
        confirmation=confirmation,
        max_items=max_items,
        max_external_calls=max_external_calls,
        max_cost_usd=max_cost_usd,
        date_window_hours=date_window_hours,
        enrich_details=enrich_details,
        force_refresh=force_refresh,
    )
    if not execute:
        return {**request, "status": "preview", "external_calls": 0, "retry_count": 0}

    actor = _authorized_viewer(
        os.environ.get("WM_END_USER_EMAIL"),
        _get_variable(WRITER_ALLOWLIST_PATH),
    )
    # Internal jobs/run requests do not inherit the app viewer. Keep execution
    # and actor attribution in this app job; never accept an actor from the UI.
    try:
        return _run_intake(request, actor)
    except Exception as error:
        # Preserve an actionable error class without exposing provider payloads.
        raise RuntimeError(f"TikHub golden intake failed ({type(error).__name__})") from None
