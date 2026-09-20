from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/test-server-stack-smoke.sh"


def test_stack_smoke_refuses_to_touch_docker_without_explicit_gate() -> None:
    result = subprocess.run(
        [str(SCRIPT)],
        text=True,
        capture_output=True,
        env={**os.environ, "TEST_SERVER_STACK_SMOKE": ""},
        check=False,
    )

    assert result.returncode == 2
    assert "TEST_SERVER_STACK_SMOKE=YES" in result.stderr


def test_stack_smoke_source_has_disposable_full_stack_and_security_contract() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'TEST_SERVER_STACK_SMOKE:-}" == YES' in source
    assert 'RUNTIME_BASE="$ROOT_DIR/work"' in source
    assert 'mktemp -d "$RUNTIME_BASE/test-server-stack-smoke.XXXXXX"' in source
    assert 'chmod 600 "$ENV_FILE"' in source
    assert 'chmod 755 "$TLS_DIR"' in source
    assert 'chmod 644 "$TLS_DIR/privkey.pem" "$TLS_DIR/fullchain.pem"' in source
    assert 'example_image WINDMILL_IMAGE' in source
    assert 'example_image POSTGRES_IMAGE' in source
    assert 'example_image NGINX_IMAGE' in source
    assert "subjectAltName=DNS:localhost,IP:127.0.0.1" in source
    assert "TEST_SERVER_HTTP_PORT=0" in source
    assert "TEST_SERVER_HTTPS_PORT=0" in source
    assert "compose up --detach postgres windmill_server windmill_worker windmill_worker_native proxy" in source
    assert "https://127.0.0.1:${proxy_https_port}/api/version?probe=${QUERY_SENTINEL}" in source
    assert "http://127.0.0.1:${proxy_http_port}/api/version?probe=${QUERY_SENTINEL}" in source
    assert "for service in postgres windmill_server windmill_worker windmill_worker_native proxy" in source
    assert "for service in postgres windmill_server windmill_worker windmill_worker_native" in source
    assert "PortBindings" in source
    assert '"$host_ip" == 127.0.0.1' in source
    assert '"$binding_count" == 2' in source
    assert "ReadonlyRootfs" in source
    assert '"ALL"' in source and '"CAP_CHOWN"' in source and '"CAP_SETGID"' in source and '"CAP_SETUID"' in source
    assert "no-new-privileges" in source
    assert "proxy logs exposed an Authorization or query sentinel" in source
    assert "proxy exited before the HTTPS readiness probe succeeded" in source
    assert "compose down --volumes --remove-orphans" in source
    assert "rm -rf -- \"$RUNTIME_DIR\"" in source
    assert "TIKHUB" not in source
    assert "VOLCENGINE" not in source
    assert "OPENAI" not in source


def test_stack_smoke_is_executable_and_does_not_accept_arguments() -> None:
    assert SCRIPT.stat().st_mode & 0o100
    result = subprocess.run(
        [str(SCRIPT), "unexpected"],
        text=True,
        capture_output=True,
        env={**os.environ, "TEST_SERVER_STACK_SMOKE": "YES"},
        check=False,
    )
    assert result.returncode == 2
    assert "accepts no arguments" in result.stderr
