from __future__ import annotations

import stat
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/test-server-release.sh"


def _env_file(tmp_path: Path, *, public: bool = False) -> Path:
    path = tmp_path / "release.env"
    path.write_text(
        "POSTGRES_USER=postgres\nPOSTGRES_DB=windmill\n"
        "RESEARCH_DB_NAME=douyin_research\nRESEARCH_DB_USER=douyin_research\n"
    )
    path.chmod(0o644 if public else 0o600)
    return path


def _run(tmp_path: Path, command: str, *extra: str) -> subprocess.CompletedProcess[str]:
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    backup_root.chmod(0o700)
    return subprocess.run(
        [str(SCRIPT), command, "--env-file", str(_env_file(tmp_path)), "--project", "research-test", "--backup-root", str(backup_root), *extra],
        text=True,
        capture_output=True,
        check=False,
    )


def test_release_script_requires_all_explicit_deployment_selectors(tmp_path: Path) -> None:
    result = subprocess.run([str(SCRIPT), "backup"], text=True, capture_output=True, check=False)
    assert result.returncode == 2
    assert "--env-file" in result.stderr

    result = _run(tmp_path, "migrate")
    # It reaches Docker only after validating all explicit selectors; a test
    # gate is evaluated after validating all selectors and before Docker is
    # contacted; this keeps the fixture test isolated from local runtimes.
    assert "TEST_SERVER_RESEARCH_MIGRATE=YES" in result.stderr
    assert "--env-file" not in result.stderr
    assert "--project" not in result.stderr
    assert "--backup-root" not in result.stderr


def test_release_script_rejects_world_readable_env_and_unsafe_backup_root(tmp_path: Path) -> None:
    env_file = _env_file(tmp_path, public=True)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    backup_root.chmod(0o700)
    result = subprocess.run(
        [str(SCRIPT), "backup", "--env-file", str(env_file), "--project", "research-test", "--backup-root", str(backup_root)],
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 2
    assert "exact mode 0600" in result.stderr

    env_file = _env_file(tmp_path)
    env_file.chmod(0o604)
    result = subprocess.run(
        [str(SCRIPT), "backup", "--env-file", str(env_file), "--project", "research-test", "--backup-root", str(backup_root)],
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 2
    assert "exact mode 0600" in result.stderr

    result = subprocess.run(
        [str(SCRIPT), "backup", "--env-file", str(_env_file(tmp_path)), "--project", "research-test", "--backup-root", "/"],
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 2
    assert "must not be /" in result.stderr

    unsafe = _env_file(tmp_path)
    unsafe.write_text(
        "POSTGRES_USER=postgres\nPOSTGRES_DB=windmill\n"
        "RESEARCH_DB_NAME=douyin_research;drop_database\nRESEARCH_DB_USER=douyin_research\n"
    )
    result = subprocess.run(
        [str(SCRIPT), "migrate", "--env-file", str(unsafe), "--project", "research-test", "--backup-root", str(backup_root)],
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 2
    assert "simple lowercase SQL identifiers" in result.stderr

    duplicate = _env_file(tmp_path)
    duplicate.write_text(duplicate.read_text() + "POSTGRES_DB=other\n")
    result = subprocess.run(
        [str(SCRIPT), "backup", "--env-file", str(duplicate), "--project", "research-test", "--backup-root", str(backup_root)],
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 2
    assert "must occur exactly once" in result.stderr

    backup_root.chmod(0o755)
    result = subprocess.run(
        [str(SCRIPT), "backup", "--env-file", str(_env_file(tmp_path)), "--project", "research-test", "--backup-root", str(backup_root)],
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 2
    assert "exact mode 0700" in result.stderr


def test_release_script_contains_fail_closed_backup_migration_and_restore_contract() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "source \"$env_file\"" not in source
    assert "--env-file \"$env_file\"" in source
    assert "--project-name \"$project\"" in source
    assert "--backup-root must be an existing absolute directory" in source
    assert "windmill.dump" in source and "research.dump" in source
    assert "command -v sha256sum" in source
    assert "shasum -a 256" in source
    assert "globals.sql" in source
    assert ".test-server-release-backup.lock" in source
    assert ".incomplete.$$" in source
    assert 'mv -T -- "$temporary" "$destination"' in source
    assert "manifest.txt" in source
    assert "compose_project" in source
    assert 'TEST_SERVER_RESEARCH_MIGRATE:-}" == YES' in source
    assert 'PGPASSWORD=\"$RESEARCH_DB_PASSWORD\" exec psql -v ON_ERROR_STOP=1 -U \"$RESEARCH_DB_USER\"' in source
    assert "'begin;'" in source and "'commit;'" in source
    assert "tableowner=current_user" in source
    assert "has_table_privilege(current_user" in source
    assert "to_regclass('public.research_brief')" in source
    assert "to_regclass('public.research_brief_run')" in source
    assert "to_regclass('public.uq_research_brief_owner_name')" in source
    assert "to_regclass('public.idx_research_brief_due')" in source
    assert "to_regclass('public.idx_research_brief_run_time')" in source
    assert "research_brief_run_brief_id_fkey" in source
    assert "select count(*) from research_brief" in source
    assert "select count(*) from research_brief_run" in source
    assert "research_brief_contract=%s" in source
    assert "project_contract=%s" in source
    assert "verify_migration_ledger prefix \"$RESTORE_DATABASE\"" in source
    assert "verify_project_contract \"$RESTORE_DATABASE\"" in source
    assert 'test-server-archive-counts.py\" project' in source
    assert "schema and migration ledger are inconsistent" in source
    assert "RESTORE_DATABASE=\"test_server_research_restore\"" in source
    assert "RESTORE_WINDMILL_DATABASE=\"test_server_windmill_restore\"" in source
    assert 'TEST_SERVER_RESTORE_DRILL:-}" == YES' in source
    assert "trap cleanup EXIT" in source
    assert "a fixed restore-drill database already exists; refusing to delete or reuse it" in source
    assert "drop database if exists ${RESTORE_DATABASE}" not in source
    assert "drop database if exists ${RESTORE_WINDMILL_DATABASE}" not in source
    assert "Windmill restore-drill key-table counts" in source
    assert "schema_migrations ledger differs" in source
    assert "ledger contains an unknown filename or changed SHA-256" in source
    assert "ledger is not a contiguous reviewed prefix" in source
    assert "filename text primary key, sha256 text not null" in source
    assert "docker-compose.test-server-external-proxy.yml" in source
    assert "reviewed test-server overlay filename" in source
    assert "test-server-external-proxy-validate.sh" in source
    assert "verify_subject_relevance_contract" in source
    assert "subject_relevance_contract=%s" in source
    assert "subject_relevance_027" in source
    assert "'7|24'" in source
    assert "027_subject_relevance_gate.sql" in source
    assert "verify_migration_ledger prefix \"$RESTORE_DATABASE\"" in source
    assert "verify_decision_loop_contract" in source
    assert "decision_loop_contract=%s" in source
    assert "decision_loop_028" in source
    assert "'8|38'" in source
    assert "028_project_decision_loop.sql" in source
    assert "028.review_evidence_integrity" in source


def test_release_verify_contract_covers_project_migration_chain_fail_closed() -> None:
    """The release gate must reject a partially applied 021--024 project schema."""
    source = SCRIPT.read_text(encoding="utf-8")
    start = source.index("verify_project_contract() {")
    end = source.index("\n}\n\ncmd_backup", start)
    contract = source[start:end]

    # 021: project tenancy and the project-to-account relation are all
    # independently required.  Checking only research_project would allow an
    # interrupted migration to be reported as deployable.
    for relation in (
        "research_organization",
        "research_project",
        "research_project_member",
        "project_account_relation",
    ):
        assert f"to_regclass('public.{relation}')" in contract

    # 022: both the candidate table and the project scopes added to existing
    # control-plane records are release requirements.
    assert "to_regclass('public.project_video_inclusion')" in contract
    for check, relation in (
        ("022.brief_project_id", "research_brief"),
        ("022.run_project_id", "pipeline_run"),
        ("022.brief_run_project_id", "research_brief_run"),
    ):
        assert check in contract
        assert f"to_regclass('public.{relation}')" in contract
    assert "022.inclusion_immutable" in contract

    # 023 predicates are the fail-closed read boundary; merely creating the
    # member and inclusion tables is not sufficient for an ACL-capable release.
    assert "to_regprocedure('public.project_actor_can_read(uuid,text)')" in contract
    assert "to_regprocedure('public.project_video_can_read(uuid,text,uuid)')" in contract
    assert "023.actor_body" in contract and "023.video_body" in contract
    assert "sha256(convert_to(prosrc,'UTF8'))" in contract

    # 024 must retain the reviewed keyset cursor index before the matrix API is
    # exposed to a test server.
    assert "to_regclass('public.idx_project_account_relation_verified_cursor')" in contract

    # Ownership and grants are catalog checks, independent from merely finding
    # the relations. The implementation may use names or resolved OIDs.
    assert "project.table_owners" in contract
    assert "tableowner=current_user" in contract
    assert "project.read_grants" in contract
    assert "has_table_privilege(current_user" in contract
    assert "project.owner_only_grants" in contract
    assert "grant_row.grantee<>relation_row.relowner" in contract

    # Keep the contract an all-or-nothing gate: a missing item must reach the
    # explicit non-zero verification failure rather than only being logged.
    assert '[[ -z "$failed" ]]' in contract
    assert "project migration contract verification failed" in contract
    assert "exit 1" in contract
    assert "verify_project_contract" in source[source.index("cmd_migrate() {"):]
    assert "cmd_verify()" in source and "verify_project_contract" in source[source.index("cmd_verify()"):]


def test_release_script_is_executable() -> None:
    assert SCRIPT.stat().st_mode & stat.S_IXUSR
