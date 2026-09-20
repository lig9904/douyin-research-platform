from __future__ import annotations

import hashlib
import json
import stat
import subprocess
from pathlib import Path

import jsonschema


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/test-server-monitoring-contract.py"
SCHEMA = ROOT / "docs/test-server-monitoring-contract-v1.schema.json"
STAMP = "2026-09-20T01:02:03Z"


def _init(tmp_path: Path) -> Path:
    path = tmp_path / "monitoring.json"
    result = subprocess.run([str(SCRIPT), "init", "--output", str(path)], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    return path


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(0o600)


def _verify(path: Path, complete: bool = False) -> subprocess.CompletedProcess[str]:
    args = [str(SCRIPT), "verify", "--input", str(path)]
    if complete:
        args.append("--require-complete")
    return subprocess.run(args, text=True, capture_output=True, check=False)


def _close_failed_monitor(contract: dict, monitor_id: str) -> None:
    contract["monitors"][monitor_id] = {"status": "fail", "checked_at_utc": STAMP, "evidence_id": f"ev_{monitor_id}"}
    contract["alerts"][monitor_id] = {
        "state": "closed", "fired_at_utc": "2026-09-20T01:00:00Z", "acknowledged_at_utc": "2026-09-20T01:01:00Z",
        "closed_at_utc": STAMP, "evidence_id": f"ev_alert_{monitor_id}",
    }


def test_init_is_0600_secret_free_and_never_overwrites(tmp_path: Path) -> None:
    path = _init(tmp_path)
    contract = _load(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert set(contract["monitors"]) == {"proxy_https", "windmill_health", "postgres_health", "backup_failure", "restore_failure"}
    assert contract["schedule"] == {"timezone": "Asia/Shanghai", "provider_egress_enabled": False, "idempotency_key": "monitoring-contract-v1", "max_concurrency": 1, "timeout_seconds": 60, "failure_policy": "disable_and_alert"}
    assert contract["delivery"] == {"mode": "disabled_no_external_delivery", "status": "not_attempted"}
    second = subprocess.run([str(SCRIPT), "init", "--output", str(path)], text=True, capture_output=True, check=False)
    assert second.returncode == 1
    assert "refusing to overwrite" in second.stderr


def test_alert_state_machine_requires_ordered_utc_timestamps(tmp_path: Path) -> None:
    path = _init(tmp_path)
    contract = _load(path)
    _close_failed_monitor(contract, "proxy_https")
    _write(path, contract)
    assert _verify(path).returncode == 0
    contract["alerts"]["proxy_https"]["closed_at_utc"] = "2026-09-20T01:01:00Z"
    _write(path, contract)
    assert "timestamps must be ordered" in _verify(path).stderr
    contract["alerts"]["proxy_https"] = {"state": "acknowledged", "fired_at_utc": STAMP, "acknowledged_at_utc": None, "closed_at_utc": None, "evidence_id": "ev_alert"}
    _write(path, contract)
    assert "invalid timestamps" in _verify(path).stderr


def test_schedule_delivery_and_monitor_alert_invariants(tmp_path: Path) -> None:
    path = _init(tmp_path)
    contract = _load(path)
    contract["schedule"]["provider_egress_enabled"] = True
    _write(path, contract)
    assert "egress must remain disabled" in _verify(path).stderr
    contract["schedule"]["provider_egress_enabled"] = False
    contract["monitors"]["postgres_health"] = {"status": "fail", "checked_at_utc": STAMP, "evidence_id": "ev_postgres"}
    _write(path, contract)
    assert "must fire an alert" in _verify(path).stderr
    contract["delivery"]["status"] = "delivered"
    _write(path, contract)
    assert "cannot claim external delivery" in _verify(path).stderr


def test_rejects_sensitive_data_without_echoing_it_and_bad_file_targets(tmp_path: Path) -> None:
    path = _init(tmp_path)
    contract = _load(path)
    sentinel = "https://token.example.invalid/do-not-echo"
    contract["schedule"]["idempotency_key"] = sentinel
    _write(path, contract)
    failed = _verify(path)
    assert failed.returncode == 1
    assert sentinel not in failed.stdout + failed.stderr
    path.chmod(0o644)
    assert "exact mode 0600" in _verify(path).stderr
    path.chmod(0o600)
    linked = tmp_path / "linked.json"
    linked.symlink_to(path)
    assert "regular non-symlink" in _verify(linked).stderr


def test_seal_complete_contract_and_draft_2020_schema(tmp_path: Path) -> None:
    path = _init(tmp_path)
    contract = _load(path)
    for monitor_id in contract["monitors"]:
        contract["monitors"][monitor_id] = {"status": "pass", "checked_at_utc": STAMP, "evidence_id": f"ev_{monitor_id}"}
    _write(path, contract)
    assert _verify(path, complete=True).returncode == 0
    seal = tmp_path / "monitoring.sha256"
    result = subprocess.run([str(SCRIPT), "seal", "--input", str(path), "--output", str(seal), "--require-complete"], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert stat.S_IMODE(seal.stat().st_mode) == 0o600
    assert seal.read_text().split()[0] == hashlib.sha256(path.read_bytes()).hexdigest()
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    validator.validate(contract)
    contract["schedule"]["max_concurrency"] = 2
    assert any(list(error.path) == ["schedule", "max_concurrency"] for error in validator.iter_errors(contract))


def test_schema_and_python_both_reject_invalid_monitor_and_alert_state(tmp_path: Path) -> None:
    path = _init(tmp_path)
    contract = _load(path)
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())

    contract["monitors"]["proxy_https"]["evidence_id"] = "ev_impossible"
    _write(path, contract)
    assert _verify(path).returncode == 1
    assert any(list(error.path)[:2] == ["monitors", "proxy_https"] for error in validator.iter_errors(contract))

    contract["monitors"]["proxy_https"] = {"status": "fail", "checked_at_utc": STAMP, "evidence_id": "ev_proxy_failure"}
    _write(path, contract)
    assert "must fire an alert" in _verify(path).stderr
    assert any(list(error.path)[:2] == ["alerts", "proxy_https"] for error in validator.iter_errors(contract))

    second_dir = tmp_path / "second"
    second_dir.mkdir()
    second_path = _init(second_dir)
    contract = _load(second_path)
    contract["alerts"]["proxy_https"] = {
        "state": "closed",
        "fired_at_utc": STAMP,
        "acknowledged_at_utc": None,
        "closed_at_utc": None,
        "evidence_id": "ev_impossible",
    }
    _write(second_path, contract)
    assert _verify(second_path).returncode == 1
    assert any(list(error.path)[:2] == ["alerts", "proxy_https"] for error in validator.iter_errors(contract))
