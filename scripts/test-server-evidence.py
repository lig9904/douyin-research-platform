#!/usr/bin/env python3
"""Create and verify a secret-free V1 test-server evidence manifest."""

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


SCHEMA_VERSION = "test-server-evidence-v1"
CHECK_IDS = (
    "gate_a_host_preflight",
    "gate_a_compose_render",
    "gate_a_https_proxy",
    "gate_a_direct_port_isolation",
    "gate_b_pre_migration_backup",
    "gate_b_migration_ledger",
    "gate_b_windmill_inventory",
    "gate_b_app_read_path",
    "gate_c_admin_acl",
    "gate_c_reviewer_acl",
    "gate_c_viewer_acl",
    "gate_c_actor_audit",
    "gate_d_log_redaction",
    "gate_e_schedule_safety",
    "gate_e_concurrency",
    "gate_e_database_restore",
    "gate_e_globals_restore",
    "gate_e_offsite_backup",
    "gate_e_alert_closure",
)
PROVIDERS = ("tikhub", "ark", "asr")
STATUSES = {"not_run", "pass", "fail", "blocked"}
COST_STATUSES = {"not_applicable", "pending", "reconciled"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
FQDN_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
EVIDENCE_ID_RE = re.compile(r"^ev_[a-z0-9][a-z0-9_-]{0,62}$")
IMAGE_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]*(?::[A-Za-z0-9._-]+)?@sha256:[0-9a-f]{64}$")
UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
FORBIDDEN_VALUE_RE = re.compile(
    r"(?:https?://|[?]|(?:^|[^a-z0-9])(?:authorization|bearer|password|secret|token|cookie|request[_ -]?body|response[_ -]?body|media[_ -]?url)(?:$|[^a-z0-9])|"
    r"(?:sk-|AKLT)[A-Za-z0-9._-]{8,}|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})",
    re.IGNORECASE,
)


class EvidenceError(ValueError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _is_utc_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not UTC_TIMESTAMP_RE.fullmatch(value):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset().total_seconds() == 0


def _exact_keys(value: Any, expected: set[str], field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise EvidenceError(f"{field} has an invalid field set")
    return value


def _nullable_sha(value: Any, field: str) -> None:
    if value is not None and (not isinstance(value, str) or not SHA256_RE.fullmatch(value)):
        raise EvidenceError(f"{field} must be null or a SHA-256")


def _nullable_evidence_id(value: Any, field: str) -> None:
    if value is not None and (not isinstance(value, str) or not EVIDENCE_ID_RE.fullmatch(value)):
        raise EvidenceError(f"{field} must be null or a redacted evidence id")


def _nullable_utc_timestamp(value: Any, field: str) -> None:
    if value is not None and not _is_utc_timestamp(value):
        raise EvidenceError(f"{field} must be null or an RFC3339 UTC timestamp")


def _scan_values(value: Any) -> None:
    if isinstance(value, dict):
        for child in value.values():
            _scan_values(child)
    elif isinstance(value, list):
        for child in value:
            _scan_values(child)
    elif isinstance(value, str):
        if len(value) > 256 or FORBIDDEN_VALUE_RE.search(value):
            raise EvidenceError("bundle contains a forbidden or unsafe string value")


def validate_bundle(bundle: Any, *, require_complete: bool = False) -> None:
    root = _exact_keys(
        bundle,
        {"schema_version", "environment", "generated_at_utc", "deployment", "checks", "providers", "backup", "monitoring"},
        "root",
    )
    if root["schema_version"] != SCHEMA_VERSION or root["environment"] != "test-server":
        raise EvidenceError("schema_version or environment is invalid")
    if not _is_utc_timestamp(root["generated_at_utc"]):
        raise EvidenceError("generated_at_utc must be an RFC3339 UTC timestamp")

    deployment = _exact_keys(
        root["deployment"],
        {"commit_sha", "compose_project", "fqdn", "images", "certificate_sha256"},
        "deployment",
    )
    if not isinstance(deployment["commit_sha"], str) or not COMMIT_RE.fullmatch(deployment["commit_sha"]):
        raise EvidenceError("deployment.commit_sha is invalid")
    if not isinstance(deployment["compose_project"], str) or not PROJECT_RE.fullmatch(deployment["compose_project"]):
        raise EvidenceError("deployment.compose_project is invalid")
    fqdn = deployment["fqdn"]
    if not isinstance(fqdn, str) or not FQDN_RE.fullmatch(fqdn) or fqdn.endswith((".example", ".invalid", ".test", ".localhost", ".local")):
        raise EvidenceError("deployment.fqdn must be a concrete public FQDN")
    images = _exact_keys(deployment["images"], {"postgres", "windmill", "proxy"}, "deployment.images")
    if any(not isinstance(image, str) or not IMAGE_RE.fullmatch(image) for image in images.values()):
        raise EvidenceError("every deployment image must be pinned by SHA-256 digest")
    _nullable_sha(deployment["certificate_sha256"], "deployment.certificate_sha256")

    checks = _exact_keys(root["checks"], set(CHECK_IDS), "checks")
    by_id: dict[str, dict[str, Any]] = {}
    for check_id, raw in checks.items():
        check = _exact_keys(raw, {"status", "checked_at_utc", "evidence_id"}, f"checks.{check_id}")
        if check["status"] not in STATUSES:
            raise EvidenceError("check status is invalid")
        checked_at = check["checked_at_utc"]
        _nullable_evidence_id(check["evidence_id"], "check.evidence_id")
        if check["status"] == "not_run":
            if checked_at is not None or check["evidence_id"] is not None:
                raise EvidenceError("not_run checks cannot carry completion evidence")
        else:
            if not _is_utc_timestamp(checked_at) or check["evidence_id"] is None:
                raise EvidenceError("executed checks require timestamp and evidence id")
        by_id[check_id] = check

    providers = _exact_keys(root["providers"], set(PROVIDERS), "providers")
    for provider_name, raw in providers.items():
        provider = _exact_keys(
            raw,
            {"approved", "status", "checked_at_utc", "evidence_id", "call_count", "cost_status", "actual_cost_microunits"},
            f"providers.{provider_name}",
        )
        if type(provider["approved"]) is not bool or provider["status"] not in STATUSES:
            raise EvidenceError("provider approval or status is invalid")
        if type(provider["call_count"]) is not int or not 0 <= provider["call_count"] <= 1:
            raise EvidenceError("provider call_count is invalid")
        if provider["cost_status"] not in COST_STATUSES:
            raise EvidenceError("provider cost_status is invalid")
        _nullable_evidence_id(provider["evidence_id"], "provider.evidence_id")
        if provider["checked_at_utc"] is not None and not _is_utc_timestamp(provider["checked_at_utc"]):
            raise EvidenceError("provider timestamp is invalid")
        actual_cost = provider["actual_cost_microunits"]
        if actual_cost is not None and (type(actual_cost) is not int or actual_cost < 0):
            raise EvidenceError("provider actual cost is invalid")
        if not provider["approved"]:
            if (
                provider["status"] != "not_run"
                or provider["checked_at_utc"] is not None
                or provider["evidence_id"] is not None
                or provider["call_count"] != 0
                or provider["cost_status"] != "not_applicable"
                or actual_cost is not None
            ):
                raise EvidenceError("unapproved providers must remain not_run with no cost")
        elif provider["status"] == "not_run":
            if (
                provider["checked_at_utc"] is not None
                or provider["evidence_id"] is not None
                or provider["call_count"] != 0
                or provider["cost_status"] != "not_applicable"
                or actual_cost is not None
            ):
                raise EvidenceError("not_run providers cannot carry execution or cost evidence")
        else:
            if not _is_utc_timestamp(provider["checked_at_utc"]) or provider["evidence_id"] is None:
                raise EvidenceError("executed provider checks require timestamp and evidence id")
        if provider["status"] == "blocked" and (
            provider["call_count"] != 0
            or provider["cost_status"] != "not_applicable"
            or actual_cost is not None
        ):
            raise EvidenceError("blocked providers cannot carry call or cost evidence")
        if provider["status"] == "pass" and (
            provider["call_count"] != 1 or provider["cost_status"] not in {"pending", "reconciled"}
        ):
            raise EvidenceError("passed providers require exactly one call and a cost state")
        if provider["call_count"] == 0 and provider["cost_status"] != "not_applicable":
            raise EvidenceError("zero-call providers cannot carry a billable cost state")
        if provider["cost_status"] == "reconciled" and actual_cost is None:
            raise EvidenceError("reconciled provider cost requires actual_cost_microunits")
        if provider["cost_status"] != "reconciled" and actual_cost is not None:
            raise EvidenceError("actual provider cost is allowed only after reconciliation")

    backup = _exact_keys(
        root["backup"],
        {
            "archive_format",
            "archive_created_at_utc",
            "archive_manifest_sha256",
            "archive_verified_at_utc",
            "globals_inventory_sha256",
            "offsite_copy_evidence_id",
            "offsite_copy_verified_at_utc",
            "database_restore_seconds",
            "database_restore_manifest_sha256",
            "globals_restore_seconds",
            "globals_restore_manifest_sha256",
            "globals_restore_inventory_sha256",
            "rpo_seconds",
        },
        "backup",
    )
    if backup["archive_format"] not in {None, "test-server-backup-v1"}:
        raise EvidenceError("backup.archive_format is invalid")
    _nullable_utc_timestamp(backup["archive_created_at_utc"], "backup.archive_created_at_utc")
    _nullable_sha(backup["archive_manifest_sha256"], "backup.archive_manifest_sha256")
    _nullable_utc_timestamp(backup["archive_verified_at_utc"], "backup.archive_verified_at_utc")
    _nullable_sha(backup["globals_inventory_sha256"], "backup.globals_inventory_sha256")
    _nullable_evidence_id(backup["offsite_copy_evidence_id"], "backup.offsite_copy_evidence_id")
    _nullable_utc_timestamp(backup["offsite_copy_verified_at_utc"], "backup.offsite_copy_verified_at_utc")
    _nullable_sha(backup["database_restore_manifest_sha256"], "backup.database_restore_manifest_sha256")
    _nullable_sha(backup["globals_restore_manifest_sha256"], "backup.globals_restore_manifest_sha256")
    _nullable_sha(backup["globals_restore_inventory_sha256"], "backup.globals_restore_inventory_sha256")
    for field in ("database_restore_seconds", "globals_restore_seconds", "rpo_seconds"):
        metric = backup[field]
        if metric is not None and (type(metric) is not int or metric < 0 or metric > 31_536_000):
            raise EvidenceError(f"backup.{field} is invalid")

    monitoring = _exact_keys(
        root["monitoring"],
        {"contract_sha256", "contract_verified_at_utc", "external_alert"},
        "monitoring",
    )
    _nullable_sha(monitoring["contract_sha256"], "monitoring.contract_sha256")
    _nullable_utc_timestamp(monitoring["contract_verified_at_utc"], "monitoring.contract_verified_at_utc")
    external_alert = _exact_keys(
        monitoring["external_alert"],
        {"state", "fired_at_utc", "acknowledged_at_utc", "closed_at_utc", "external_delivery_evidence_id"},
        "monitoring.external_alert",
    )
    alert_state = external_alert["state"]
    if alert_state not in {"not_run", "fired", "acknowledged", "closed"}:
        raise EvidenceError("monitoring.external_alert.state is invalid")
    alert_timestamp_fields = ("fired_at_utc", "acknowledged_at_utc", "closed_at_utc")
    for field in alert_timestamp_fields:
        _nullable_utc_timestamp(external_alert[field], f"monitoring.external_alert.{field}")
    _nullable_evidence_id(
        external_alert["external_delivery_evidence_id"],
        "monitoring.external_alert.external_delivery_evidence_id",
    )
    expected_alert_timestamps = {
        "not_run": set(),
        "fired": {"fired_at_utc"},
        "acknowledged": {"fired_at_utc", "acknowledged_at_utc"},
        "closed": set(alert_timestamp_fields),
    }[alert_state]
    present_alert_timestamps = {field for field in alert_timestamp_fields if external_alert[field] is not None}
    if present_alert_timestamps != expected_alert_timestamps:
        raise EvidenceError("monitoring external alert state has invalid timestamps")
    if alert_state == "not_run":
        if external_alert["external_delivery_evidence_id"] is not None:
            raise EvidenceError("not_run external alert cannot carry delivery evidence")
    else:
        if external_alert["external_delivery_evidence_id"] is None:
            raise EvidenceError("executed external alert requires delivery evidence")
        ordered_alert_times = [
            datetime.fromisoformat(external_alert[field][:-1] + "+00:00")
            for field in alert_timestamp_fields
            if external_alert[field] is not None
        ]
        if any(left >= right for left, right in zip(ordered_alert_times, ordered_alert_times[1:])):
            raise EvidenceError("monitoring external alert timestamps must be strictly ordered")

    if by_id["gate_a_https_proxy"]["status"] == "pass" and deployment["certificate_sha256"] is None:
        raise EvidenceError("HTTPS pass requires certificate_sha256")
    if by_id["gate_b_pre_migration_backup"]["status"] == "pass" and (
        backup["archive_format"] != "test-server-backup-v1"
        or backup["archive_created_at_utc"] is None
        or backup["archive_manifest_sha256"] is None
        or backup["archive_verified_at_utc"] is None
        or backup["globals_inventory_sha256"] is None
    ):
        raise EvidenceError("backup pass requires verified test-server-backup-v1 digests and timestamp")
    if (
        by_id["gate_e_schedule_safety"]["status"] == "pass"
        or by_id["gate_e_concurrency"]["status"] == "pass"
    ) and (monitoring["contract_sha256"] is None or monitoring["contract_verified_at_utc"] is None):
        raise EvidenceError("schedule and concurrency pass require a verified monitoring contract")
    if by_id["gate_e_database_restore"]["status"] == "pass" and (
        backup["database_restore_seconds"] is None
        or backup["archive_format"] != "test-server-backup-v1"
        or backup["archive_created_at_utc"] is None
        or backup["archive_verified_at_utc"] is None
        or backup["archive_manifest_sha256"] is None
        or backup["database_restore_manifest_sha256"] != backup["archive_manifest_sha256"]
    ):
        raise EvidenceError("database restore pass requires measured duration bound to the verified archive")
    if by_id["gate_e_globals_restore"]["status"] == "pass" and (
        backup["globals_restore_seconds"] is None
        or backup["archive_format"] != "test-server-backup-v1"
        or backup["archive_created_at_utc"] is None
        or backup["archive_verified_at_utc"] is None
        or backup["archive_manifest_sha256"] is None
        or backup["globals_inventory_sha256"] is None
        or backup["globals_restore_manifest_sha256"] != backup["archive_manifest_sha256"]
        or backup["globals_restore_inventory_sha256"] != backup["globals_inventory_sha256"]
    ):
        raise EvidenceError("globals restore pass requires measured duration bound to the verified archive and inventory")
    if by_id["gate_e_offsite_backup"]["status"] == "pass" and (
        backup["offsite_copy_evidence_id"] is None
        or backup["offsite_copy_verified_at_utc"] is None
        or backup["archive_verified_at_utc"] is None
        or backup["rpo_seconds"] is None
    ):
        raise EvidenceError("offsite backup pass requires archive and offsite verification evidence")
    if by_id["gate_e_alert_closure"]["status"] == "pass" and alert_state != "closed":
        raise EvidenceError("alert closure pass requires a closed externally delivered alert lifecycle")

    _scan_values(root)
    if require_complete:
        if any(check["status"] != "pass" for check in by_id.values()):
            raise EvidenceError("required checks are not all pass")
        for provider in providers.values():
            if provider["approved"] and provider["status"] != "pass":
                raise EvidenceError("approved provider evidence is incomplete")


def _read_bundle(path: Path) -> tuple[Any, bytes]:
    descriptor: int | None = None
    try:
        if path.is_symlink():
            raise EvidenceError("evidence bundle must be a regular non-symlink file")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise EvidenceError("evidence bundle must be a regular non-symlink file")
        if metadata.st_size > 1_048_576:
            raise EvidenceError("evidence bundle exceeds 1 MiB")
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            raise EvidenceError("evidence bundle must have exact mode 0600")
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = None
            raw = handle.read(1_048_577)
        if len(raw) > 1_048_576:
            raise EvidenceError("evidence bundle exceeds 1 MiB")
        return json.loads(raw), raw
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceError("evidence bundle is unreadable or invalid JSON") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _write_new(path: Path, content: str) -> None:
    if not path.is_absolute() or not path.parent.is_dir():
        raise EvidenceError("output must be an absolute path in an existing directory")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise EvidenceError("refusing to overwrite an existing output") from exc
    except OSError as exc:
        raise EvidenceError("unable to create the requested output securely") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _initial_bundle(args: argparse.Namespace) -> dict[str, Any]:
    checks = {
        check_id: {"status": "not_run", "checked_at_utc": None, "evidence_id": None}
        for check_id in CHECK_IDS
    }
    providers = {
        provider: {
            "approved": False,
            "status": "not_run",
            "checked_at_utc": None,
            "evidence_id": None,
            "call_count": 0,
            "cost_status": "not_applicable",
            "actual_cost_microunits": None,
        }
        for provider in PROVIDERS
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "environment": "test-server",
        "generated_at_utc": _utc_now(),
        "deployment": {
            "commit_sha": args.commit_sha,
            "compose_project": args.compose_project,
            "fqdn": args.fqdn,
            "images": {"postgres": args.postgres_image, "windmill": args.windmill_image, "proxy": args.proxy_image},
            "certificate_sha256": None,
        },
        "checks": checks,
        "providers": providers,
        "backup": {
            "archive_format": None,
            "archive_created_at_utc": None,
            "archive_manifest_sha256": None,
            "archive_verified_at_utc": None,
            "globals_inventory_sha256": None,
            "offsite_copy_evidence_id": None,
            "offsite_copy_verified_at_utc": None,
            "database_restore_seconds": None,
            "database_restore_manifest_sha256": None,
            "globals_restore_seconds": None,
            "globals_restore_manifest_sha256": None,
            "globals_restore_inventory_sha256": None,
            "rpo_seconds": None,
        },
        "monitoring": {
            "contract_sha256": None,
            "contract_verified_at_utc": None,
            "external_alert": {
                "state": "not_run",
                "fired_at_utc": None,
                "acknowledged_at_utc": None,
                "closed_at_utc": None,
                "external_delivery_evidence_id": None,
            },
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    init_parser = subparsers.add_parser("init", help="create a mode-0600 not-run evidence manifest")
    init_parser.add_argument("--output", type=Path, required=True)
    init_parser.add_argument("--commit-sha", required=True)
    init_parser.add_argument("--compose-project", required=True)
    init_parser.add_argument("--fqdn", required=True)
    init_parser.add_argument("--postgres-image", required=True)
    init_parser.add_argument("--windmill-image", required=True)
    init_parser.add_argument("--proxy-image", required=True)
    verify_parser = subparsers.add_parser("verify", help="validate without printing evidence content")
    verify_parser.add_argument("--input", type=Path, required=True)
    verify_parser.add_argument("--require-complete", action="store_true")
    seal_parser = subparsers.add_parser("seal", help="validate and write a separate SHA-256 file")
    seal_parser.add_argument("--input", type=Path, required=True)
    seal_parser.add_argument("--output", type=Path, required=True)
    seal_parser.add_argument("--require-complete", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "init":
            bundle = _initial_bundle(args)
            validate_bundle(bundle)
            _write_new(args.output, json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            print("EVIDENCE_INIT created mode=0600 status=not_run")
        elif args.command == "verify":
            bundle, _ = _read_bundle(args.input)
            validate_bundle(bundle, require_complete=args.require_complete)
            print(f"EVIDENCE_VALID complete={'true' if args.require_complete else 'not_required'}")
        else:
            bundle, raw = _read_bundle(args.input)
            validate_bundle(bundle, require_complete=args.require_complete)
            digest = hashlib.sha256(raw).hexdigest()
            _write_new(args.output, f"{digest}  {args.input.name}\n")
            print("EVIDENCE_SEALED sha256_written=true")
    except EvidenceError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except OSError:
        print("ERROR: filesystem operation failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
