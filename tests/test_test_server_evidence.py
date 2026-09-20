from __future__ import annotations

import hashlib
import json
import stat
import subprocess
from pathlib import Path

import jsonschema


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/test-server-evidence.py"
SCHEMA = ROOT / "docs/test-server-evidence-v1.schema.json"
STAMP = "2026-09-20T01:02:03Z"


def _init(tmp_path: Path) -> Path:
    output = tmp_path / "evidence.json"
    result = subprocess.run(
        [
            str(SCRIPT),
            "init",
            "--output", str(output),
            "--commit-sha", "a" * 40,
            "--compose-project", "douyin-research-test",
            "--fqdn", "research.acme.dev",
            "--postgres-image", "postgres@sha256:" + "1" * 64,
            "--windmill-image", "ghcr.io/windmill-labs/windmill@sha256:" + "2" * 64,
            "--proxy-image", "nginx@sha256:" + "3" * 64,
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return output


def _run_verify(path: Path, *, complete: bool = False) -> subprocess.CompletedProcess[str]:
    command = [str(SCRIPT), "verify", "--input", str(path)]
    if complete:
        command.append("--require-complete")
    return subprocess.run(command, text=True, capture_output=True, check=False)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, bundle: dict) -> None:
    path.write_text(json.dumps(bundle), encoding="utf-8")
    path.chmod(0o600)


def _complete(bundle: dict) -> None:
    bundle["deployment"]["certificate_sha256"] = "4" * 64
    for check in bundle["checks"].values():
        check["status"] = "pass"
        check["checked_at_utc"] = STAMP
    for check_id, check in bundle["checks"].items():
        check["evidence_id"] = "ev_" + check_id
    bundle["backup"].update(
        {
            "archive_format": "test-server-backup-v1",
            "archive_created_at_utc": "2026-09-20T00:00:00Z",
            "archive_manifest_sha256": "5" * 64,
            "archive_verified_at_utc": "2026-09-20T00:01:00Z",
            "globals_inventory_sha256": "6" * 64,
            "offsite_copy_evidence_id": "ev_offsite_copy",
            "offsite_copy_verified_at_utc": "2026-09-20T00:10:00Z",
            "database_restore_seconds": 12,
            "database_restore_manifest_sha256": "5" * 64,
            "globals_restore_seconds": 8,
            "globals_restore_manifest_sha256": "5" * 64,
            "globals_restore_inventory_sha256": "6" * 64,
            "rpo_seconds": 60,
        }
    )
    bundle["monitoring"]["contract_sha256"] = "7" * 64
    bundle["monitoring"]["contract_verified_at_utc"] = "2026-09-20T00:20:00Z"
    bundle["monitoring"]["external_alert"] = {
        "state": "closed",
        "fired_at_utc": "2026-09-20T00:21:00Z",
        "acknowledged_at_utc": "2026-09-20T00:22:00Z",
        "closed_at_utc": "2026-09-20T00:23:00Z",
        "external_delivery_evidence_id": "ev_external_alert_delivery",
    }


def test_init_is_secret_free_mode_0600_and_never_overwrites(tmp_path: Path) -> None:
    output = _init(tmp_path)
    bundle = _load(output)

    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert bundle["schema_version"] == "test-server-evidence-v1"
    assert len(bundle["checks"]) == 19
    assert set(bundle["checks"]) == {
        "gate_a_host_preflight", "gate_a_compose_render", "gate_a_https_proxy", "gate_a_direct_port_isolation",
        "gate_b_pre_migration_backup", "gate_b_migration_ledger", "gate_b_windmill_inventory", "gate_b_app_read_path",
        "gate_c_admin_acl", "gate_c_reviewer_acl", "gate_c_viewer_acl", "gate_c_actor_audit",
        "gate_d_log_redaction", "gate_e_schedule_safety", "gate_e_concurrency", "gate_e_database_restore",
        "gate_e_globals_restore", "gate_e_offsite_backup", "gate_e_alert_closure",
    }
    assert {item["status"] for item in bundle["checks"].values()} == {"not_run"}
    assert "password" not in output.read_text(encoding="utf-8").lower()
    assert bundle["deployment"]["proxy_mode"] == "container"

    second = subprocess.run(
        [str(SCRIPT), "init", "--output", str(output), "--commit-sha", "a" * 40,
         "--compose-project", "douyin-research-test", "--fqdn", "research.acme.dev",
         "--postgres-image", "postgres@sha256:" + "1" * 64,
         "--windmill-image", "windmill@sha256:" + "2" * 64,
         "--proxy-image", "nginx@sha256:" + "3" * 64],
        text=True, capture_output=True, check=False,
    )
    assert second.returncode == 1
    assert "refusing to overwrite" in second.stderr


def test_verify_distinguishes_valid_template_from_complete_acceptance(tmp_path: Path) -> None:
    output = _init(tmp_path)
    assert _run_verify(output).returncode == 0

    incomplete = _run_verify(output, complete=True)
    assert incomplete.returncode == 1
    assert "not all pass" in incomplete.stderr

    bundle = _load(output)
    _complete(bundle)
    _write(output, bundle)
    complete = _run_verify(output, complete=True)
    assert complete.returncode == 0, complete.stderr
    assert complete.stdout.strip() == "EVIDENCE_VALID complete=true"


def test_approved_provider_requires_one_bounded_call_and_cost_state(tmp_path: Path) -> None:
    output = _init(tmp_path)
    bundle = _load(output)
    _complete(bundle)
    provider = bundle["providers"]["ark"]
    provider.update(
        {
            "approved": True,
            "status": "pass",
            "checked_at_utc": STAMP,
            "evidence_id": "ev_ark_smoke",
            "call_count": 1,
            "cost_status": "pending",
        }
    )
    _write(output, bundle)
    assert _run_verify(output, complete=True).returncode == 0

    provider["call_count"] = 2
    _write(output, bundle)
    failed = _run_verify(output)
    assert failed.returncode == 1
    assert "call_count is invalid" in failed.stderr


def test_rejects_inconsistent_not_run_and_non_utc_evidence(tmp_path: Path) -> None:
    output = _init(tmp_path)
    bundle = _load(output)
    check = bundle["checks"]["gate_a_host_preflight"]
    check["evidence_id"] = "ev_should_not_exist"
    _write(output, bundle)
    assert "not_run checks" in _run_verify(output).stderr

    check["status"] = "pass"
    check["checked_at_utc"] = "2026-09-20"
    _write(output, bundle)
    assert "executed checks require" in _run_verify(output).stderr


def test_restore_and_monitoring_gates_are_bound_to_real_evidence(tmp_path: Path) -> None:
    output = _init(tmp_path)
    bundle = _load(output)
    _complete(bundle)

    bundle["backup"]["database_restore_manifest_sha256"] = "8" * 64
    _write(output, bundle)
    assert "bound to the verified archive" in _run_verify(output).stderr

    bundle["backup"]["database_restore_manifest_sha256"] = "5" * 64
    bundle["backup"]["globals_restore_inventory_sha256"] = "9" * 64
    _write(output, bundle)
    assert "archive and inventory" in _run_verify(output).stderr

    bundle["backup"]["globals_restore_inventory_sha256"] = "6" * 64
    bundle["monitoring"]["contract_sha256"] = None
    _write(output, bundle)
    assert "verified monitoring contract" in _run_verify(output).stderr

    bundle["monitoring"]["contract_sha256"] = "7" * 64
    bundle["monitoring"]["external_alert"] = {
        "state": "not_run",
        "fired_at_utc": None,
        "acknowledged_at_utc": None,
        "closed_at_utc": None,
        "external_delivery_evidence_id": None,
    }
    _write(output, bundle)
    assert "closed externally delivered" in _run_verify(output).stderr

def test_single_restore_pass_still_requires_verified_archive_metadata(tmp_path: Path) -> None:
    output = _init(tmp_path)
    bundle = _load(output)
    check = bundle["checks"]["gate_e_database_restore"]
    check.update({"status": "pass", "checked_at_utc": STAMP, "evidence_id": "ev_database_restore"})
    bundle["backup"]["database_restore_seconds"] = 1
    bundle["backup"]["archive_manifest_sha256"] = "5" * 64
    bundle["backup"]["database_restore_manifest_sha256"] = "5" * 64
    _write(output, bundle)
    assert "bound to the verified archive" in _run_verify(output).stderr


def test_external_alert_lifecycle_requires_delivery_and_strict_time_order(tmp_path: Path) -> None:
    output = _init(tmp_path)
    bundle = _load(output)
    alert = bundle["monitoring"]["external_alert"]
    alert.update({"state": "closed", "fired_at_utc": STAMP, "acknowledged_at_utc": STAMP, "closed_at_utc": STAMP})
    _write(output, bundle)
    assert "requires delivery evidence" in _run_verify(output).stderr

    alert["external_delivery_evidence_id"] = "ev_external_delivery"
    _write(output, bundle)
    assert "strictly ordered" in _run_verify(output).stderr


def test_rejects_sensitive_value_without_echoing_it(tmp_path: Path) -> None:
    output = _init(tmp_path)
    bundle = _load(output)
    sentinel = "ev_bearer_token_do_not_echo"
    bundle["checks"]["gate_a_host_preflight"]["status"] = "blocked"
    bundle["checks"]["gate_a_host_preflight"]["checked_at_utc"] = STAMP
    bundle["checks"]["gate_a_host_preflight"]["evidence_id"] = sentinel
    _write(output, bundle)

    result = _run_verify(output)

    assert result.returncode == 1
    assert "forbidden or unsafe" in result.stderr
    assert sentinel not in result.stdout + result.stderr


def test_rejects_missing_checks_extra_fields_and_public_file_mode(tmp_path: Path) -> None:
    output = _init(tmp_path)
    bundle = _load(output)
    bundle["checks"].pop("gate_a_host_preflight")
    _write(output, bundle)
    assert "invalid field set" in _run_verify(output).stderr

    bundle = _load(output)
    bundle["unexpected"] = True
    _write(output, bundle)
    assert "invalid field set" in _run_verify(output).stderr

    bundle.pop("unexpected")
    _write(output, bundle)
    output.chmod(0o644)
    assert "exact mode 0600" in _run_verify(output).stderr

    output.chmod(0o600)
    linked = tmp_path / "linked.json"
    linked.symlink_to(output)
    assert "regular non-symlink" in _run_verify(linked).stderr


def test_seal_writes_only_hash_and_schema_is_valid_json(tmp_path: Path) -> None:
    output = _init(tmp_path)
    bundle = _load(output)
    _complete(bundle)
    _write(output, bundle)
    seal = tmp_path / "evidence.sha256"

    result = subprocess.run(
        [str(SCRIPT), "seal", "--input", str(output), "--output", str(seal), "--require-complete"],
        text=True, capture_output=True, check=False,
    )

    assert result.returncode == 0, result.stderr
    assert stat.S_IMODE(seal.stat().st_mode) == 0o600
    assert seal.read_text().split()[0] == hashlib.sha256(output.read_bytes()).hexdigest()
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert schema["additionalProperties"] is False
    checks = schema["properties"]["checks"]
    assert checks["additionalProperties"] is False
    assert len(checks["required"]) == 19


def test_json_schema_executes_draft_2020_contract(tmp_path: Path) -> None:
    output = _init(tmp_path)
    bundle = _load(output)
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())

    validator.validate(bundle)
    _complete(bundle)
    validator.validate(bundle)

    bundle["deployment"]["fqdn"] = "research.example.invalid"
    assert any(list(error.path) == ["deployment", "fqdn"] for error in validator.iter_errors(bundle))

    bundle["deployment"]["fqdn"] = "research.acme.dev"
    bundle["providers"]["ark"]["call_count"] = 1
    assert any(list(error.path)[:2] == ["providers", "ark"] for error in validator.iter_errors(bundle))


def test_external_proxy_evidence_does_not_claim_a_local_proxy_image(tmp_path: Path) -> None:
    output = tmp_path / "external.json"
    result = subprocess.run(
        [
            str(SCRIPT), "init",
            "--output", str(output),
            "--commit-sha", "a" * 40,
            "--compose-project", "douyin-research-test",
            "--fqdn", "research.acme.dev",
            "--postgres-image", "postgres@sha256:" + "1" * 64,
            "--windmill-image", "windmill@sha256:" + "2" * 64,
            "--proxy-mode", "external",
        ],
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    bundle = _load(output)
    assert bundle["deployment"]["proxy_mode"] == "external"
    assert bundle["deployment"]["images"]["proxy"] is None
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(bundle)

    bundle["deployment"]["images"]["proxy"] = "nginx@sha256:" + "3" * 64
    _write(output, bundle)
    assert "must not claim" in _run_verify(output).stderr


def test_container_proxy_requires_a_pinned_proxy_image(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            str(SCRIPT), "init",
            "--output", str(tmp_path / "container.json"),
            "--commit-sha", "a" * 40,
            "--compose-project", "douyin-research-test",
            "--fqdn", "research.acme.dev",
            "--postgres-image", "postgres@sha256:" + "1" * 64,
            "--windmill-image", "windmill@sha256:" + "2" * 64,
        ],
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 1
    assert "requires a proxy image" in result.stderr
    assert not (tmp_path / "container.json").exists()


def test_legacy_container_bundle_without_proxy_mode_remains_valid(tmp_path: Path) -> None:
    output = _init(tmp_path)
    bundle = _load(output)
    bundle["deployment"].pop("proxy_mode")
    _write(output, bundle)

    assert _run_verify(output).returncode == 0
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(bundle)


def test_init_rejects_reserved_domain_and_unpinned_image(tmp_path: Path) -> None:
    common = [
        str(SCRIPT), "init", "--commit-sha", "a" * 40,
        "--compose-project", "douyin-research-test",
        "--windmill-image", "windmill@sha256:" + "2" * 64,
        "--proxy-image", "nginx@sha256:" + "3" * 64,
    ]
    reserved = subprocess.run(
        [*common, "--output", str(tmp_path / "reserved.json"), "--fqdn", "research.example.invalid", "--postgres-image", "postgres@sha256:" + "1" * 64],
        text=True, capture_output=True, check=False,
    )
    assert reserved.returncode == 1
    assert "concrete public FQDN" in reserved.stderr
    assert not (tmp_path / "reserved.json").exists()

    tag_only = subprocess.run(
        [*common, "--output", str(tmp_path / "tag.json"), "--fqdn", "research.acme.dev", "--postgres-image", "postgres:17"],
        text=True, capture_output=True, check=False,
    )
    assert tag_only.returncode == 1
    assert "pinned by SHA-256" in tag_only.stderr
    assert not (tmp_path / "tag.json").exists()
