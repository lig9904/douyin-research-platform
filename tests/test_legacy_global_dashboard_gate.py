from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path("windmill/f/content_research/research_dashboard.raw_app/backend")
BACKENDS = (
    "get_home_overview.py",
    "get_daily_briefing.py",
    "get_operations_overview.py",
)


def _load(filename: str):
    path = ROOT / filename
    spec = importlib.util.spec_from_file_location(f"legacy_gate_{path.stem}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("filename", BACKENDS)
@pytest.mark.parametrize(
    "email,allowlist",
    (
        ("", '["admin@example.com"]'),
        ("viewer@example.com", '["admin@example.com"]'),
        ("admin@example.com", ""),
        ("admin@example.com", "not-json-but-not-an-email"),
    ),
)
def test_legacy_global_reads_fail_before_database_connection(
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
    email: str,
    allowlist: str,
) -> None:
    """A client cannot enumerate the old global dashboard without server ACL."""
    module = _load(filename)
    calls = 0

    def unexpected_connect(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("database must not be opened for a denied request")

    monkeypatch.setattr(module.psycopg, "connect", unexpected_connect)
    monkeypatch.setenv("WM_END_USER_EMAIL", email)
    monkeypatch.setitem(
        sys.modules,
        "wmill",
        SimpleNamespace(get_variable=lambda _path: allowlist),
    )

    with pytest.raises(PermissionError, match="LEGACY_ADMIN_ACCESS_REQUIRED"):
        module.main({"host": "unused", "port": 5432, "user": "unused", "password": "", "dbname": "unused", "sslmode": "disable"})
    assert calls == 0


@pytest.mark.parametrize("filename", BACKENDS)
def test_legacy_global_reads_fail_closed_when_allowlist_variable_is_missing(
    monkeypatch: pytest.MonkeyPatch, filename: str
) -> None:
    module = _load(filename)
    connect_calls = 0

    def unexpected_connect(*_args, **_kwargs):
        nonlocal connect_calls
        connect_calls += 1
        raise AssertionError("database must not be opened for a denied request")

    def missing_variable(_path: str) -> str:
        raise RuntimeError("Windmill variable is absent")

    monkeypatch.setattr(module.psycopg, "connect", unexpected_connect)
    monkeypatch.setenv("WM_END_USER_EMAIL", "admin@example.com")
    monkeypatch.setitem(sys.modules, "wmill", SimpleNamespace(get_variable=missing_variable))

    with pytest.raises(PermissionError, match="LEGACY_ADMIN_ACCESS_REQUIRED"):
        module.main({"host": "unused", "port": 5432, "user": "unused", "password": "", "dbname": "unused", "sslmode": "disable"})
    assert connect_calls == 0


@pytest.mark.parametrize("filename", BACKENDS)
def test_legacy_global_reads_accept_only_server_allowlisted_admin(
    monkeypatch: pytest.MonkeyPatch, filename: str
) -> None:
    module = _load(filename)
    requested: list[str] = []

    def get_variable(path: str) -> str:
        requested.append(path)
        return '["admin@example.com", "other@example.com"]'

    monkeypatch.setenv("WM_END_USER_EMAIL", "ADMIN@example.com")
    monkeypatch.setitem(sys.modules, "wmill", SimpleNamespace(get_variable=get_variable))

    assert module._require_legacy_admin() == "admin@example.com"
    assert requested == ["f/content_research/research_action_writers"]
