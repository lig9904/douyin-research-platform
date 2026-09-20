from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
EXTERNAL_PROXY_EXAMPLE = ROOT / ".env.test-server-external-proxy.example"
EXTERNAL_PROXY_VALIDATOR = ROOT / "scripts/test-server-external-proxy-validate.sh"


def test_test_server_overlay_exposes_only_the_tls_proxy() -> None:
    source = (ROOT / "docker-compose.test-server.yml").read_text(encoding="utf-8")

    assert source.count("ports: !reset []") == 2
    assert "TEST_SERVER_HTTPS_PORT" in source
    assert "TEST_SERVER_TLS_DIR:?" in source
    assert "no-new-privileges:true" in source
    assert "cap_drop: [ALL]" in source
    assert "TIKHUB_API_KEY" not in source
    assert "VOLCENGINE" not in source


def test_external_proxy_overlay_has_no_local_tls_proxy_or_database_port() -> None:
    source = (ROOT / "docker-compose.test-server-external-proxy.yml").read_text(encoding="utf-8")

    assert source.count("ports: !reset []") == 1
    assert "ports: !override" in source
    assert "TEST_SERVER_WINDMILL_BIND_HOST:?" in source
    assert "${TEST_SERVER_WINDMILL_PORT:-8000}:8000" in source
    assert "0.0.0.0" not in source
    assert "proxy:" not in source
    assert "image: nginx" not in source.lower()
    assert "deploy:" in source
    assert "replicas: 2" in source
    assert "NUM_WORKERS:" not in source
    assert "TIKHUB_API_KEY" not in source


def test_shared_default_worker_cache_skips_image_copy_for_two_replica_startup() -> None:
    source = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "source: windmill_worker_cache" in source
    assert "target: /tmp/windmill/cache" in source
    assert "nocopy: true" in source
    assert "- windmill_worker_cache:/tmp/windmill/cache" not in source


def test_external_proxy_profile_is_git_safe_and_has_a_private_bind_example() -> None:
    example = EXTERNAL_PROXY_EXAMPLE.read_text(encoding="utf-8")
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "TEST_SERVER_WINDMILL_BIND_HOST=10.255.255.10" in example
    assert "TEST_SERVER_WINDMILL_PORT=8000" in example
    assert "NGINX_IMAGE=" not in example
    assert "TEST_SERVER_TLS_DIR=" not in example
    assert "TIKHUB_API_KEY=" not in example
    assert "!.env.test-server-external-proxy.example" in ignore


def test_external_proxy_validator_rejects_public_and_wildcard_binds() -> None:
    source = EXTERNAL_PROXY_VALIDATOR.read_text(encoding="utf-8")

    assert "must be an RFC1918 private IPv4 address" in source
    assert "TEST_SERVER_WINDMILL_PORT must be exactly 8000" in source
    assert "config --quiet" in source
    assert "BLOCKED: a reachable Docker daemon" in source
    assert "ip -4 -o addr show" in source
    assert "--skip-local-bind-check is restricted to CI" in source
    assert EXTERNAL_PROXY_VALIDATOR.stat().st_mode & 0o100
    assert "WINDMILL_INTERNAL_URL must exactly equal http://windmill_server:8000" in source
    assert "must be a digest-pinned image reference" in source
    assert "RESEARCH_DB_NAME" in source and "RESEARCH_DB_USER" in source


def test_external_proxy_linux_smoke_contract_is_provider_free() -> None:
    smoke = ROOT / "scripts/test-server-external-proxy-stack-smoke.sh"
    source = smoke.read_text(encoding="utf-8")

    assert smoke.stat().st_mode & 0o100
    assert "TEST_SERVER_EXTERNAL_PROXY_STACK_SMOKE=YES" in source
    assert "ip -4 -o addr show scope global" in source
    assert "windmill_server windmill_worker windmill_worker_native" in source
    assert "expected exactly two isolated default worker replicas" in source
    assert "HostConfig.PortBindings" in source
    assert '.["8000/tcp"] | length == 1' in source
    assert "TIKHUB" not in source and "VOLCENGINE" not in source


def test_external_proxy_validator_rejects_public_bind_before_compose(tmp_path: Path) -> None:
    env_file = tmp_path / "external-proxy.env"
    env_file.write_text(
        EXTERNAL_PROXY_EXAMPLE.read_text(encoding="utf-8")
        .replace("https://research.example.invalid", "https://research-ci.example.com")
        .replace("CHANGE_ME_POSTGRES_HEX", "a" * 48)
        .replace("CHANGE_ME_RESEARCH_HEX", "b" * 48)
        .replace("TEST_SERVER_WINDMILL_BIND_HOST=10.255.255.10", "TEST_SERVER_WINDMILL_BIND_HOST=0.0.0.0"),
        encoding="utf-8",
    )
    env_file.chmod(0o600)

    result = subprocess.run(
        [str(EXTERNAL_PROXY_VALIDATOR), "--env-file", str(env_file)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "must be an RFC1918 private IPv4 address" in result.stderr


def test_external_proxy_validator_rejects_placeholder_example_and_accepts_bound_credentials(tmp_path: Path) -> None:
    env_file = tmp_path / "external-proxy.env"
    example = EXTERNAL_PROXY_EXAMPLE.read_text(encoding="utf-8")
    env_file.write_text(example, encoding="utf-8")
    env_file.chmod(0o600)

    result = subprocess.run(
        [str(EXTERNAL_PROXY_VALIDATOR), "--env-file", str(env_file)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert "POSTGRES_PASSWORD is missing or still a placeholder" in result.stderr

    # Replacing only the public URL must still fail closed on credentials.
    env_file.write_text(
        example.replace("https://research.example.invalid", "https://research-ci.example.com"),
        encoding="utf-8",
    )
    result = subprocess.run(
        [str(EXTERNAL_PROXY_VALIDATOR), "--env-file", str(env_file)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert "POSTGRES_PASSWORD is missing or still a placeholder" in result.stderr

    postgres_password = "a" * 48
    research_password = "b" * 48
    base_valid = (
        example.replace("https://research.example.invalid", "https://research-ci.example.com")
        .replace("CHANGE_ME_POSTGRES_HEX", postgres_password)
        .replace("CHANGE_ME_RESEARCH_HEX", research_password)
    )
    env_file.write_text(base_valid.replace("RESEARCH_DB_PASSWORD=" + research_password, "RESEARCH_DB_PASSWORD=CHANGE_ME_RESEARCH_HEX"), encoding="utf-8")
    result = subprocess.run(
        [str(EXTERNAL_PROXY_VALIDATOR), "--env-file", str(env_file)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert "RESEARCH_DB_PASSWORD is missing or still a placeholder" in result.stderr

    no_docker_bin = tmp_path / "no-docker-bin"
    no_docker_bin.mkdir()
    unavailable_docker = no_docker_bin / "docker"
    unavailable_docker.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    unavailable_docker.chmod(0o755)
    env_file.write_text(base_valid, encoding="utf-8")
    result = subprocess.run(
        [str(EXTERNAL_PROXY_VALIDATOR), "--env-file", str(env_file), "--skip-local-bind-check"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "CI": "true", "PATH": f"{no_docker_bin}:{os.environ['PATH']}"},
    )
    assert result.returncode == 3
    assert "BLOCKED: a reachable Docker daemon" in result.stderr

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"${1:-}\" == info ]]; then exit 0; fi\n"
        "if [[ \"${1:-}\" == compose && \"${2:-}\" == version ]]; then exit 0; fi\n"
        "if [[ \"$*\" == *' config --quiet'* ]]; then exit 0; fi\n"
        "exit 2\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    result = subprocess.run(
        [str(EXTERNAL_PROXY_VALIDATOR), "--env-file", str(env_file), "--skip-local-bind-check"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "CI": "true", "PATH": f"{fake_bin}:{os.environ['PATH']}"},
    )
    assert result.returncode == 0, result.stderr
    assert "PASS: external-proxy environment" in result.stdout

    env_file.write_text(
        base_valid.replace(
            "WINDMILL_BASE_URL=https://research-ci.example.com",
            "WINDMILL_BASE_URL=https://research-ci.example.com:6443",
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [str(EXTERNAL_PROXY_VALIDATOR), "--env-file", str(env_file), "--skip-local-bind-check"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "CI": "true", "PATH": f"{fake_bin}:{os.environ['PATH']}"},
    )
    assert result.returncode == 0, result.stderr
    assert "PASS: external-proxy environment" in result.stdout

    env_file.write_text(
        base_valid.replace(
            "WINDMILL_BASE_URL=https://research-ci.example.com",
            "WINDMILL_BASE_URL=https://research-ci.example.com:70000",
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [str(EXTERNAL_PROXY_VALIDATOR), "--env-file", str(env_file)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert "port must be between 1 and 65535" in result.stderr

    env_file.write_text(
        base_valid.replace(
            "WINDMILL_BASE_URL=https://research-ci.example.com",
            "WINDMILL_BASE_URL=https://research-ci.example.com:",
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [str(EXTERNAL_PROXY_VALIDATOR), "--env-file", str(env_file)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert "optionally with a valid port" in result.stderr

    env_file.write_text(base_valid.replace("@postgres:5432/windmill", "@postgres:5432/not_windmill"), encoding="utf-8")
    result = subprocess.run(
        [str(EXTERNAL_PROXY_VALIDATOR), "--env-file", str(env_file)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert "WINDMILL_DATABASE_URL must exactly bind" in result.stderr


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


def test_postgres_readiness_never_accepts_the_temporary_socket_server() -> None:
    expected = "pg_isready -h 127.0.0.1"
    paths = (
        "docker-compose.yml",
        "scripts/test-server-release.sh",
        "scripts/local-l3-env.sh",
        "scripts/local-security-env.sh",
        ".github/workflows/infra-validate.yml",
        ".github/workflows/provider-tests.yml",
    )

    for relative_path in paths:
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert expected in source, relative_path
