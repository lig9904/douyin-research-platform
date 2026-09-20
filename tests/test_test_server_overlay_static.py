from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_test_server_overlay_exposes_only_the_tls_proxy() -> None:
    source = (ROOT / "docker-compose.test-server.yml").read_text(encoding="utf-8")

    assert source.count("ports: !reset []") == 2
    assert "TEST_SERVER_HTTPS_PORT" in source
    assert "TEST_SERVER_TLS_DIR:?" in source
    assert "no-new-privileges:true" in source
    assert "cap_drop: [ALL]" in source
    assert "TIKHUB_API_KEY" not in source
    assert "VOLCENGINE" not in source


def test_test_server_proxy_logs_no_query_or_credentials() -> None:
    source = (ROOT / "ops/test-server/nginx.conf").read_text(encoding="utf-8")
    log_format = next(line for line in source.splitlines() if "log_format" in line)

    assert "$uri" in log_format
    for forbidden in ("$request ", "$args", "$request_uri", "$http_authorization", "$cookie"):
        assert forbidden not in log_format
    assert "error_log /dev/null crit" in source
    assert "return 444" in source
    assert "https://$host" not in source
    assert "ssl_protocols TLSv1.2 TLSv1.3" in source
    assert "proxy_set_header X-Forwarded-Proto https" in source


def test_test_server_example_is_non_runnable_and_git_safe() -> None:
    example = (ROOT / ".env.test-server.example").read_text(encoding="utf-8")
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "example.invalid" in example
    assert "CHANGE_ME_POSTGRES_HEX" in example
    assert "CHANGE_ME_RESEARCH_HEX" in example
    assert "TEST_SERVER_TLS_DIR=/CHANGE_ME_ABSOLUTE_TLS_DIRECTORY" in example
    assert "TIKHUB_API_KEY=" not in example
    assert "!.env.test-server.example" in ignore
