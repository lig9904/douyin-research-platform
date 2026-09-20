import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/backup.sh"


def _write_fake_docker(bin_dir: Path) -> None:
    docker = bin_dir / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$DOCKER_CALL_LOG"
if [[ "${FAKE_DOCKER_FAIL:-}" == "1" && "$*" == *"windmill"* ]]; then exit 18; fi
case "$*" in
  *pg_dumpall*) printf 'globals dump\\n' ;;
  *" pg_dump "*) printf 'database dump\\n' ;;
  *) exit 19 ;;
esac
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)

    date = bin_dir / "date"
    date.write_text("#!/usr/bin/env bash\nprintf '%s\\n' '20260920T000000Z'\n", encoding="utf-8")
    date.chmod(0o755)


def _run(tmp_path: Path, *extra: str, fail: bool = False):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_docker(bin_dir)
    env_file = tmp_path / "server.env"
    env_file.write_text(
        "POSTGRES_USER=postgres\nPOSTGRES_DB=windmill\nRESEARCH_DB_NAME=douyin_research\n"
        "POSTGRES_PASSWORD=DO_NOT_PRINT\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    output_root = tmp_path / "out"
    output_root.mkdir(exist_ok=True)
    call_log = tmp_path / "docker-calls.log"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "DOCKER_CALL_LOG": str(call_log),
        "FAKE_DOCKER_FAIL": "1" if fail else "",
    }
    result = subprocess.run(
        [str(SCRIPT), "--env-file", str(env_file), "--project", "research-test", "--output-root", str(output_root), *extra],
        text=True, capture_output=True, env=env, cwd=tmp_path, check=False,
    )
    return result, output_root, call_log


def test_backup_is_atomic_has_dual_dumps_manifest_and_does_not_echo_env_secrets(tmp_path: Path) -> None:
    result, output_root, call_log = _run(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "DO_NOT_PRINT" not in result.stdout + result.stderr
    completed = [path for path in output_root.iterdir() if not path.name.startswith(".")]
    assert len(completed) == 1
    directory = completed[0]
    assert {path.name for path in directory.iterdir()} == {"globals.sql", "windmill.dump", "douyin_research.dump", "manifest.txt"}
    manifest = (directory / "manifest.txt").read_text(encoding="utf-8")
    assert "format=windmill-research-backup-v2" in manifest
    assert manifest.count("sha256=") == 3
    calls = call_log.read_text(encoding="utf-8")
    assert calls.count("pg_dumpall") == 1
    assert calls.count(" pg_dump ") == 2
    assert "--env-file" in calls and "--project-name research-test" in calls
    assert f"--project-directory {ROOT}" in calls
    assert f"-f {ROOT / 'docker-compose.yml'}" in calls
    assert not list(output_root.glob(".incomplete-*"))


def test_failed_dump_leaves_no_completed_or_staging_directory(tmp_path: Path) -> None:
    result, output_root, _ = _run(tmp_path, fail=True)

    assert result.returncode != 0
    assert output_root.exists()
    assert not [path for path in output_root.iterdir() if not path.name.startswith(".")]
    assert not list(output_root.glob(".incomplete-*"))
    assert not (output_root / ".backup.lock").exists()


def test_timestamp_collision_never_removes_an_existing_completed_backup(tmp_path: Path) -> None:
    output_root = tmp_path / "out"
    output_root.mkdir()
    existing = output_root / "20260920T000000Z"
    existing.mkdir()
    marker = existing / "keep-me"
    marker.write_text("existing verified backup", encoding="utf-8")

    result, _, _ = _run(tmp_path, "--output-root", str(output_root))

    assert result.returncode != 0
    assert marker.read_text(encoding="utf-8") == "existing verified backup"
    assert not (output_root / ".backup.lock").exists()


def test_publish_executable_is_opt_in_and_never_evaled(tmp_path: Path) -> None:
    marker = tmp_path / "publish-ran"
    publisher = tmp_path / "publisher"
    publisher.write_text(f"#!/usr/bin/env bash\nprintf '%s' \"$1\" > '{marker}'\n", encoding="utf-8")
    publisher.chmod(0o755)

    result, _, _ = _run(tmp_path, "--publish-executable", str(publisher))

    assert result.returncode == 0, result.stderr
    assert not marker.exists()
    assert "configured but not run" in result.stdout
    assert "eval" not in SCRIPT.read_text(encoding="utf-8")


def test_backup_source_never_sources_env_and_requires_0600() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "source .env" not in source
    assert '"$(file_mode \"$ENV_FILE\")" == "600"' in source
    assert "--env-file" in source and "--project" in source and "--output-root" in source
    assert 'mv -T -- "$STAGE_DIR" "$FINAL_DIR"' in source
    assert '--project-directory "$ROOT_DIR"' in source
