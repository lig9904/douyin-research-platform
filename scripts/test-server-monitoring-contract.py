#!/usr/bin/env python3
"""Create and verify a local-only, secret-free test-server monitoring contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "test-server-monitoring-contract-v1"
MONITOR_IDS = (
    "proxy_https",
    "windmill_health",
    "postgres_health",
    "backup_failure",
    "restore_failure",
)
MONITOR_STATUSES = {"not_run", "pass", "fail", "blocked"}
ALERT_STATES = {"not_run", "fired", "acknowledged", "closed"}
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
EVIDENCE_RE = re.compile(r"^ev_[a-z0-9][a-z0-9_-]{0,62}$")
IDEMPOTENCY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,62}$")
FORBIDDEN_RE = re.compile(
    r"(?:https?://|[?]|(?:^|[^a-z0-9])(?:webhook|e-?mail|url|secret|password|token|cookie|authorization|bearer|request[_ -]?id|request[_ -]?body|response[_ -]?body)(?:$|[^a-z0-9])|"
    r"(?:sk-|AKLT)[A-Za-z0-9._-]{8,}|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})",
    re.IGNORECASE,
)


class ContractError(ValueError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _is_utc(value: Any) -> bool:
    if not isinstance(value, str) or not UTC_RE.fullmatch(value):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset().total_seconds() == 0


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value[:-1] + "+00:00")


def _exact(value: Any, keys: set[str], field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ContractError(f"{field} has an invalid field set")
    return value


def _evidence(value: Any, field: str) -> None:
    if value is not None and (not isinstance(value, str) or not EVIDENCE_RE.fullmatch(value)):
        raise ContractError(f"{field} must be null or a redacted evidence id")


def _scan(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if FORBIDDEN_RE.search(key):
                raise ContractError("contract contains a forbidden field")
            _scan(child)
    elif isinstance(value, list):
        for child in value:
            _scan(child)
    elif isinstance(value, str) and (len(value) > 256 or FORBIDDEN_RE.search(value)):
        raise ContractError("contract contains a forbidden or unsafe string value")


def _validate_alert(alert: Any, monitor_id: str) -> None:
    alert = _exact(
        alert,
        {"state", "fired_at_utc", "acknowledged_at_utc", "closed_at_utc", "evidence_id"},
        f"alerts.{monitor_id}",
    )
    state = alert["state"]
    if state not in ALERT_STATES:
        raise ContractError("alert state is invalid")
    _evidence(alert["evidence_id"], "alert.evidence_id")
    timestamps = ("fired_at_utc", "acknowledged_at_utc", "closed_at_utc")
    for field in timestamps:
        if alert[field] is not None and not _is_utc(alert[field]):
            raise ContractError("alert timestamp must be an RFC3339 UTC timestamp")
    expected_present = {
        "not_run": set(),
        "fired": {"fired_at_utc"},
        "acknowledged": {"fired_at_utc", "acknowledged_at_utc"},
        "closed": set(timestamps),
    }[state]
    present = {field for field in timestamps if alert[field] is not None}
    if present != expected_present:
        raise ContractError("alert state has invalid timestamps")
    if state == "not_run":
        if alert["evidence_id"] is not None:
            raise ContractError("not_run alerts cannot carry evidence")
        return
    if alert["evidence_id"] is None:
        raise ContractError("active alerts require a redacted evidence id")
    ordered = [_parse_utc(alert[field]) for field in timestamps if alert[field] is not None]
    if any(left >= right for left, right in zip(ordered, ordered[1:])):
        raise ContractError("alert UTC timestamps must be ordered")


def validate_contract(contract: Any, *, require_complete: bool = False) -> None:
    root = _exact(
        contract,
        {"schema_version", "environment", "generated_at_utc", "schedule", "monitors", "alerts", "delivery"},
        "root",
    )
    if root["schema_version"] != SCHEMA_VERSION or root["environment"] != "test-server":
        raise ContractError("schema_version or environment is invalid")
    if not _is_utc(root["generated_at_utc"]):
        raise ContractError("generated_at_utc must be an RFC3339 UTC timestamp")

    schedule = _exact(
        root["schedule"],
        {"timezone", "provider_egress_enabled", "idempotency_key", "max_concurrency", "timeout_seconds", "failure_policy"},
        "schedule",
    )
    if schedule["timezone"] != "Asia/Shanghai":
        raise ContractError("schedule timezone must be Asia/Shanghai")
    if schedule["provider_egress_enabled"] is not False:
        raise ContractError("provider egress must remain disabled")
    if not isinstance(schedule["idempotency_key"], str) or not IDEMPOTENCY_RE.fullmatch(schedule["idempotency_key"]):
        raise ContractError("schedule idempotency_key is invalid")
    if schedule["max_concurrency"] != 1:
        raise ContractError("schedule max_concurrency must be 1")
    if type(schedule["timeout_seconds"]) is not int or not 1 <= schedule["timeout_seconds"] <= 300:
        raise ContractError("schedule timeout_seconds must be between 1 and 300")
    if schedule["failure_policy"] != "disable_and_alert":
        raise ContractError("schedule failure_policy must disable and alert")

    delivery = _exact(root["delivery"], {"mode", "status"}, "delivery")
    if delivery != {"mode": "disabled_no_external_delivery", "status": "not_attempted"}:
        raise ContractError("local contract cannot claim external delivery")

    monitors = _exact(root["monitors"], set(MONITOR_IDS), "monitors")
    alerts = _exact(root["alerts"], set(MONITOR_IDS), "alerts")
    for monitor_id in MONITOR_IDS:
        monitor = _exact(monitors[monitor_id], {"status", "checked_at_utc", "evidence_id"}, f"monitors.{monitor_id}")
        status = monitor["status"]
        if status not in MONITOR_STATUSES:
            raise ContractError("monitor status is invalid")
        _evidence(monitor["evidence_id"], "monitor.evidence_id")
        if status == "not_run":
            if monitor["checked_at_utc"] is not None or monitor["evidence_id"] is not None:
                raise ContractError("not_run monitors cannot carry evidence")
        elif not _is_utc(monitor["checked_at_utc"]) or monitor["evidence_id"] is None:
            raise ContractError("executed monitors require UTC timestamp and evidence id")
        _validate_alert(alerts[monitor_id], monitor_id)
        alert_state = alerts[monitor_id]["state"]
        if status in {"not_run", "blocked"} and alert_state != "not_run":
            raise ContractError("not_run or blocked monitors cannot claim an alert transition")
        if status == "fail" and alert_state == "not_run":
            raise ContractError("failed monitor must fire an alert")
        if status == "pass" and alert_state in {"fired", "acknowledged"}:
            raise ContractError("passing monitor requires a closed or not_run alert")

    _scan(root)
    if require_complete:
        if any(monitors[item]["status"] not in {"pass", "fail"} for item in MONITOR_IDS):
            raise ContractError("all monitors must be executed for complete verification")
        if any(alerts[item]["state"] not in {"not_run", "closed"} for item in MONITOR_IDS):
            raise ContractError("complete verification requires closed or not_run alerts")


def _read(path: Path) -> tuple[Any, bytes]:
    descriptor: int | None = None
    try:
        if path.is_symlink():
            raise ContractError("monitoring contract must be a regular non-symlink file")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ContractError("monitoring contract must be a regular non-symlink file")
        if metadata.st_size > 1_048_576:
            raise ContractError("monitoring contract exceeds 1 MiB")
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            raise ContractError("monitoring contract must have exact mode 0600")
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = None
            raw = handle.read(1_048_577)
        if len(raw) > 1_048_576:
            raise ContractError("monitoring contract exceeds 1 MiB")
        return json.loads(raw), raw
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("monitoring contract is unreadable or invalid JSON") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _write_new(path: Path, content: str) -> None:
    if not path.is_absolute() or not path.parent.is_dir():
        raise ContractError("output must be an absolute path in an existing directory")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ContractError("refusing to overwrite an existing output") from exc
    except OSError as exc:
        raise ContractError("unable to create the requested output securely") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _initial_contract(timeout_seconds: int) -> dict[str, Any]:
    pending = {"status": "not_run", "checked_at_utc": None, "evidence_id": None}
    alert = {"state": "not_run", "fired_at_utc": None, "acknowledged_at_utc": None, "closed_at_utc": None, "evidence_id": None}
    return {
        "schema_version": SCHEMA_VERSION,
        "environment": "test-server",
        "generated_at_utc": _utc_now(),
        "schedule": {
            "timezone": "Asia/Shanghai",
            "provider_egress_enabled": False,
            "idempotency_key": "monitoring-contract-v1",
            "max_concurrency": 1,
            "timeout_seconds": timeout_seconds,
            "failure_policy": "disable_and_alert",
        },
        "monitors": {item: dict(pending) for item in MONITOR_IDS},
        "alerts": {item: dict(alert) for item in MONITOR_IDS},
        "delivery": {"mode": "disabled_no_external_delivery", "status": "not_attempted"},
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="create a mode-0600 local-only monitoring contract")
    init.add_argument("--output", type=Path, required=True)
    init.add_argument("--timeout-seconds", type=int, default=60)
    verify = sub.add_parser("verify", help="validate without external notification")
    verify.add_argument("--input", type=Path, required=True)
    verify.add_argument("--require-complete", action="store_true")
    seal = sub.add_parser("seal", help="validate and write a SHA-256 sidecar")
    seal.add_argument("--input", type=Path, required=True)
    seal.add_argument("--output", type=Path, required=True)
    seal.add_argument("--require-complete", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "init":
            contract = _initial_contract(args.timeout_seconds)
            validate_contract(contract)
            _write_new(args.output, json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            print("MONITORING_CONTRACT_INIT created mode=0600 delivery=disabled")
        elif args.command == "verify":
            contract, _ = _read(args.input)
            validate_contract(contract, require_complete=args.require_complete)
            print(f"MONITORING_CONTRACT_VALID complete={'true' if args.require_complete else 'not_required'} delivery=disabled")
        else:
            contract, raw = _read(args.input)
            validate_contract(contract, require_complete=args.require_complete)
            _write_new(args.output, f"{hashlib.sha256(raw).hexdigest()}  {args.input.name}\n")
            print("MONITORING_CONTRACT_SEALED sha256_written=true delivery=disabled")
    except ContractError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except OSError:
        print("ERROR: filesystem operation failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
