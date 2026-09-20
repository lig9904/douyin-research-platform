from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/test-server-globals-restore-drill.sh"
RELEASE = ROOT / "scripts/test-server-release.sh"
POSTGRES_IMAGE = "postgres@sha256:86c951e05bf56c93d95d397747fb8820ac76cc3bedb78f43abd83eedbe3666ae"


def _backup(tmp_path: Path, *, tablespace: bool = False) -> tuple[Path, Path]:
    root = tmp_path / "backups"
    root.mkdir(mode=0o700, parents=True)
    root.chmod(0o700)
    directory = root / "20260920T000000Z"
    directory.mkdir(mode=0o700)
    directory.chmod(0o700)
    sql = """
CREATE ROLE postgres;
ALTER ROLE postgres WITH SUPERUSER INHERIT CREATEROLE CREATEDB LOGIN NOREPLICATION NOBYPASSRLS CONNECTION LIMIT -1;
CREATE ROLE restore_demo LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 3;
CREATE ROLE restore_group NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT -1;
GRANT restore_group TO restore_demo WITH ADMIN TRUE, INHERIT FALSE, SET FALSE;
"""
    if tablespace:
        sql += "CREATE TABLESPACE unsafe_location LOCATION '/not/allowed';\n"
    inventory = "\n".join(
        [
            "member|726573746f72655f67726f7570|726573746f72655f64656d6f|706f737467726573|true|false|false",
            "role|706f737467726573|true|true|true|true|true|false|false|-1|",
            "role|726573746f72655f64656d6f|false|true|false|false|true|false|false|3|",
            "role|726573746f72655f67726f7570|false|true|false|false|false|false|false|-1|",
            "",
        ]
    )
    contents = {
        "globals.sql": sql,
        "globals.inventory": inventory,
        "windmill.dump": "test-only-placeholder\n",
        "research.dump": "test-only-placeholder\n",
    }
    digests: dict[str, str] = {}
    for name, value in contents.items():
        path = directory / name
        path.write_text(value, encoding="utf-8")
        path.chmod(0o600)
        digests[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (directory / "SHA256SUMS").write_text(
        "".join(f"{digests[name]}  {name}\n" for name in sorted(digests)), encoding="utf-8"
    )
    (directory / "manifest.txt").write_text(
        "\n".join(
            [
                "format=test-server-backup-v1",
                "created_at_utc=20260920T000000Z",
                "compose_project=research-test",
                "research_database=douyin_research",
                "windmill_database=windmill",
                "postgres_role=postgres",
                f"globals_inventory_sha256={digests['globals.inventory']}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    for name in ("SHA256SUMS", "manifest.txt"):
        (directory / name).chmod(0o600)
    return root, directory


def _run(root: Path, directory: Path, *, enabled: bool = False) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if enabled:
        env["TEST_SERVER_GLOBALS_RESTORE_DRILL"] = "YES"
    return subprocess.run(
        [str(SCRIPT), "--backup-root", str(root), "--postgres-image", POSTGRES_IMAGE, str(directory)],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )


def _docker_restore_resources() -> tuple[set[str], set[str]]:
    containers = subprocess.run(
        ["docker", "ps", "-a", "--filter", "name=test-server-globals-restore-", "--format", "{{.Names}}"],
        text=True, capture_output=True, check=True,
    )
    volumes = subprocess.run(
        ["docker", "volume", "ls", "--filter", "name=test-server-globals-restore-", "--format", "{{.Name}}"],
        text=True, capture_output=True, check=True,
    )
    return set(containers.stdout.splitlines()), set(volumes.stdout.splitlines())


def test_globals_restore_requires_an_explicit_switch_before_docker(tmp_path: Path) -> None:
    root, directory = _backup(tmp_path)
    result = _run(root, directory)
    assert result.returncode == 2
    assert "TEST_SERVER_GLOBALS_RESTORE_DRILL=YES" in result.stderr


def test_globals_restore_rejects_incomplete_or_unsafe_archives_before_docker(tmp_path: Path) -> None:
    root, directory = _backup(tmp_path, tablespace=True)
    result = _run(root, directory, enabled=True)
    assert result.returncode == 1
    assert "tablespaces" in result.stderr
    assert "unsafe_location" not in result.stderr

    root, directory = _backup(tmp_path / "missing")
    (directory / "globals.inventory").unlink()
    result = _run(root, directory, enabled=True)
    assert result.returncode == 1
    assert "incomplete" in result.stderr

    root, directory = _backup(tmp_path / "mode")
    (directory / "globals.sql").chmod(0o644)
    result = _run(root, directory, enabled=True)
    assert result.returncode == 1
    assert "exact mode 0600" in result.stderr


def test_globals_restore_and_backup_contracts_are_secret_free_and_pinned() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    release = RELEASE.read_text(encoding="utf-8")
    assert "TEST_SERVER_GLOBALS_RESTORE_DRILL" in source
    assert "--network none" in source
    assert "--read-only" in source
    assert "--cap-drop ALL" in source
    assert "--security-opt no-new-privileges:true" in source
    assert "--publish" not in source
    assert "rolpassword" not in source
    assert "rolconfig" not in source
    assert "globals.inventory" in source and "cmp -s" in source
    assert 'POSTGRES_USER=$source_postgres_role' in source
    assert 'ON_ERROR_STOP=1 -U "$source_postgres_role"' in source
    assert "ON_ERROR_STOP=0" not in source
    assert 'CREATE ROLE ${source_postgres_role};' in source
    assert "create[[:space:]]+tablespace" in source
    assert "test-server-backup-v1" in source
    assert "test-server-backup-verify.py" in source
    assert "sha256sum -c" not in source and "shasum -a 256 -c" not in source
    assert "cleanup was incomplete" in source
    assert "globals_inventory_sha256" in release
    assert "write_globals_inventory" in release
    assert "membership.inherit_option" in release and "membership.set_option" in release
    assert "membership.inherit_option" in source and "membership.set_option" in source
    assert "rolpassword" in release and "rolconfig" in release
    assert "format=test-server-backup-v1" in release
    assert "test-server-backup-verify.py" in release
    assert "sha256sum -c" not in release and "shasum -a 256 -c" not in release


@pytest.mark.skipif(
    os.environ.get("RUN_TEST_SERVER_GLOBALS_RESTORE_INTEGRATION") != "YES" or shutil.which("docker") is None,
    reason="requires explicit disposable Docker integration opt-in",
)
def test_globals_restore_runs_in_one_disposable_isolated_postgres(tmp_path: Path) -> None:
    root, directory = _backup(tmp_path)
    before = _docker_restore_resources()
    result = _run(root, directory, enabled=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("GLOBALS_RESTORE_DRILL_VALID format=test-server-backup-v1 manifest_sha256=")
    assert " inventory_sha256=" in result.stdout and " duration_seconds=" in result.stdout
    assert str(directory) not in result.stdout
    assert not list(root.glob(".globals-restore.*"))
    assert not (Path("/var/run/docker.sock").exists() and "docker.sock" in result.stdout)
    assert _docker_restore_resources() == before


@pytest.mark.skipif(
    os.environ.get("RUN_TEST_SERVER_GLOBALS_RESTORE_INTEGRATION") != "YES" or shutil.which("docker") is None,
    reason="requires explicit disposable Docker integration opt-in",
)
def test_globals_restore_failure_leaves_no_container_volume_or_runtime_dir(tmp_path: Path) -> None:
    root, directory = _backup(tmp_path)
    sql = directory / "globals.sql"
    sql.write_text("CREATE ROLE postgres;\nthis is invalid SQL;\n", encoding="utf-8")
    sql.chmod(0o600)
    digest = hashlib.sha256(sql.read_bytes()).hexdigest()
    sums = directory / "SHA256SUMS"
    lines = [f"{digest}  globals.sql\n" if line.endswith("  globals.sql\n") else line for line in sums.read_text().splitlines(keepends=True)]
    sums.write_text("".join(lines), encoding="utf-8")
    sums.chmod(0o600)
    before = _docker_restore_resources()

    result = _run(root, directory, enabled=True)

    assert result.returncode == 1
    assert "globals SQL restore command failed" in result.stderr
    assert _docker_restore_resources() == before
    assert not list(root.glob(".globals-restore.*"))


def test_globals_restore_script_is_executable() -> None:
    assert SCRIPT.stat().st_mode & stat.S_IXUSR
