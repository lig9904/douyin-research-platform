from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_security_compose_is_loopback_https_only_and_resets_plaintext_port() -> None:
    source = (ROOT / "docker-compose.local-security.yml").read_text()
    assert "127.0.0.1" in source
    assert "LOCAL_SECURITY_HTTPS_PORT:-28443" in source
    assert "LOCAL_SECURITY_POSTGRES_PORT:-25432" in source
    assert "ports: !reset []" in source
    assert "proxy:" in source


def test_proxy_has_tls_rate_limit_and_explicit_safe_log_format() -> None:
    source = (ROOT / "ops/local-security/nginx.conf").read_text()
    assert "ssl_certificate" in source
    assert "TLSv1.2 TLSv1.3" in source
    assert "limit_req_zone" in source
    assert "limit_req_status 429" in source
    safe_line = next(line for line in source.splitlines() if "log_format local_safe" in line)
    assert "$uri" in safe_line
    assert "$request " not in safe_line
    assert "$request_uri" not in safe_line
    assert "Authorization" not in safe_line
    assert "$http_" not in safe_line
    assert "error_log /dev/null crit;" in source


def test_acl_probe_uses_real_windmill_roles_and_rolls_back_synthetic_rows() -> None:
    source = (ROOT / "ops/local-security/verify-folder-acl.sql").read_text()
    assert "SET LOCAL ROLE windmill_user" in source
    assert "session.user" in source
    assert "session.pgroups" in source
    assert "admin visibility mismatch" in source
    assert "reviewer visibility mismatch" in source
    assert "viewer visibility mismatch" in source
    assert "ROLLBACK;" in source


def test_lifecycle_uses_own_context_never_sources_env_and_has_restore_guards() -> None:
    source = (ROOT / "scripts/local-security-env.sh").read_text()
    assert "CONTEXT=colima-l3-security-local" in source
    assert "PROJECT=l3-security-local" in source
    assert "--context \"$CONTEXT\" compose -p \"$PROJECT\"" in source
    assert "source \"$ENV_FILE\"" not in source
    assert "Authorization redaction" in source
    assert "query sentinel" in source
    assert "SHA256SUMS" in source
    assert "local_security_windmill_restore" in source
    assert "DROP DATABASE IF EXISTS $rw WITH (FORCE)" in source
