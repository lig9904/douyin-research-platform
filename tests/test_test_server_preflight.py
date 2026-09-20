from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/test-server-preflight.sh"


def _fake_bin(tmp_path: Path, name: str, body: str) -> None:
    target = tmp_path / name
    target.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body)
    target.chmod(target.stat().st_mode | stat.S_IXUSR)


def _fixture_env(tmp_path: Path, *, dns: bool = True, port_occupied: bool = False) -> dict[str, str]:
    _fake_bin(tmp_path, "uname", 'printf "%s\\n" "${PREFLIGHT_UNAME:-Linux}"\n')
    _fake_bin(
        tmp_path,
        "docker",
        '''case "$1" in
  --version) echo 'Docker version fixture' ;;
  info) echo 'fixture-server' ;;
  compose) test "$2" = version; echo 'Docker Compose fixture' ;;
  *) exit 2 ;;
esac
''',
    )
    _fake_bin(tmp_path, "df", "printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\\n/dev/fake 1 1 26214400 1%% /\\n'\n")
    _fake_bin(tmp_path, "ss", "if [[ ${PREFLIGHT_PORT_OCCUPIED:-0} == 1 ]]; then echo 'LISTEN 0 1 *:443 *:*'; fi\n")
    _fake_bin(tmp_path, "getent", "[[ ${PREFLIGHT_DNS:-1} == 1 ]] && echo '203.0.113.10 STREAM deploy.example.com'\n")
    _fake_bin(tmp_path, "curl", "printf '%s' \"${PREFLIGHT_HTTP_STATUS:-401}\"\n")
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{tmp_path}:{env['PATH']}",
            "PREFLIGHT_DNS": "1" if dns else "0",
            "PREFLIGHT_PORT_OCCUPIED": "1" if port_occupied else "0",
        }
    )
    return env


def _run(tmp_path: Path, *args: str, **env_overrides: str) -> subprocess.CompletedProcess[str]:
    env = _fixture_env(tmp_path)
    env.update(env_overrides)
    return subprocess.run(
        [str(SCRIPT), "--domain", "deploy.acme.dev", *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def test_preflight_passes_against_local_command_fixtures(tmp_path: Path) -> None:
    result = _run(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "PASS Linux host detected" in result.stdout
    assert "PASS Docker client and daemon are available" in result.stdout
    assert "PASS required TCP port 80 is free" in result.stdout
    assert "PASS outbound HTTPS reachable: registry-1.docker.io (401)" in result.stdout
    assert "RESULT passed warnings=0" in result.stdout


def test_preflight_reports_busy_port_and_does_not_mutate_fixture(tmp_path: Path) -> None:
    result = _run(tmp_path, PREFLIGHT_PORT_OCCUPIED="1")

    assert result.returncode == 1
    assert "FAIL required TCP port 443 is already listening" in result.stderr
    assert "RESULT failed=1 warnings=0" in result.stderr


def test_preflight_can_warn_for_pending_dns_but_rejects_placeholder_domain(tmp_path: Path) -> None:
    env = _fixture_env(tmp_path, dns=False)
    result = subprocess.run(
        [str(SCRIPT), "--domain", "deploy.acme.dev", "--allow-pending-dns", "--skip-egress"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0
    assert "WARN DNS does not yet resolve deploy.acme.dev" in result.stderr
    assert "WARN outbound HTTPS checks were explicitly skipped" in result.stderr

    invalid = subprocess.run(
        [str(SCRIPT), "--domain", "host.example", "--skip-egress"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert invalid.returncode == 2
    assert "not a placeholder" in invalid.stderr

    reserved = subprocess.run(
        [str(SCRIPT), "--domain", "deploy.acme.test", "--skip-egress"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert reserved.returncode == 2
    assert "not a placeholder" in reserved.stderr


def test_preflight_rejects_egress_urls_that_can_carry_credentials(tmp_path: Path) -> None:
    result = _run(tmp_path, "--egress-url", "https://user:secret@example.com/api")

    assert result.returncode == 1
    assert "credential-free HTTPS" in result.stderr
    assert "secret" not in result.stdout


def test_preflight_is_explicitly_read_only_and_has_no_secret_inputs() -> None:
    source = SCRIPT.read_text()

    assert "never opens an SSH connection, changes host state" in source
    assert "docker --version" in source
    assert "docker info" in source
    assert "docker compose version" in source
    assert "df -Pk /" in source
    assert "ss -ltnH" in source
    assert "getent ahosts" in source
    assert "--egress-url" in source
    assert "--domain" in source
    assert "mkdir" not in source
    assert "touch " not in source
    assert "SECRET" not in source
