"""Authenticated app dispatcher for the bounded TikHub golden intake."""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any
from urllib import parse, request


CONFIRMATION = "RUN_TIKHUB_GOLDEN_PAID"
SCRIPT_PATH = "f/content_research/collectors/manual_golden_intake"
DB_RESOURCE = "$res:f/content_research/research_db"
WRITER_ALLOWLIST_PATH = "f/content_research/research_action_writers"


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


def _run_child(args: dict[str, Any]) -> dict[str, Any]:
    params = {}
    if os.environ.get("WM_JOB_ID"):
        params["parent_job"] = os.environ["WM_JOB_ID"]
    if os.environ.get("WM_ROOT_FLOW_JOB_ID"):
        params["root_job"] = os.environ["WM_ROOT_FLOW_JOB_ID"]
    query = f"?{parse.urlencode(params)}" if params else ""
    path = parse.quote(SCRIPT_PATH, safe="/")
    job_id = _api("POST", f"w/{_workspace()}/jobs/run/p/{path}{query}", args)
    if not isinstance(job_id, str) or not job_id:
        raise RuntimeError("Windmill internal dispatch failed")

    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        state = _api(
            "GET",
            f"w/{_workspace()}/jobs_u/completed/get_result_maybe/{parse.quote(job_id, safe='')}",
        )
        if isinstance(state, dict) and state.get("completed"):
            if state.get("success") and isinstance(state.get("result"), dict):
                return state["result"]
            raise RuntimeError("TikHub golden intake failed")
        time.sleep(0.5)
    _api(
        "POST",
        f"w/{_workspace()}/jobs_u/queue/cancel/{parse.quote(job_id, safe='')}",
        {"reason": "parent app timeout"},
    )
    raise TimeoutError("TikHub golden intake timed out")


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

    _authorized_viewer(
        os.environ.get("WM_END_USER_EMAIL"),
        _get_variable(WRITER_ALLOWLIST_PATH),
    )
    # The child script owns the database resource, Secret read, advisory lock,
    # budget gate, provider call and sanitized result. Windmill links this child
    # to the app job so its authenticated end-user context is preserved.
    child_request = {"db": DB_RESOURCE, **request}
    if child_request["max_cost_usd"] is None:
        child_request.pop("max_cost_usd")
    result = _run_child(child_request)
    if not isinstance(result, dict) or result.get("status") != "completed":
        raise RuntimeError("TikHub golden intake failed")
    return result
