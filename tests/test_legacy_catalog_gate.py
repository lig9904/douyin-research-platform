from __future__ import annotations

from contextlib import nullcontext
import importlib.util
import inspect
import sys
import types
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "windmill/f/content_research/research_dashboard.raw_app/backend"
DENIED = "RESEARCH_LEGACY_CATALOG_ACCESS_DENIED"
STEMS = ("get_account_library", "get_hotspot_library", "search_research")


def _load(stem: str):
    windmill_root = str((ROOT / "windmill").resolve())
    if windmill_root not in sys.path:
        sys.path.insert(0, windmill_root)
    path = BACKEND / f"{stem}.py"
    spec = importlib.util.spec_from_file_location(f"legacy_catalog_gate_{stem}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _install_wmill(monkeypatch: pytest.MonkeyPatch, get_variable) -> None:
    monkeypatch.setitem(sys.modules, "wmill", types.SimpleNamespace(get_variable=get_variable))


@pytest.mark.parametrize("stem", STEMS)
def test_legacy_catalog_gate_rejects_before_any_database_connection(
    monkeypatch: pytest.MonkeyPatch, stem: str,
) -> None:
    module = _load(stem)
    monkeypatch.delenv("WM_END_USER_EMAIL", raising=False)

    def unexpected_database(*args, **kwargs):
        raise AssertionError("legacy catalogue must reject before a database connection")

    monkeypatch.setattr(module.psycopg, "connect", unexpected_database)
    with pytest.raises(PermissionError, match=DENIED):
        module.main({"host": "must-not-connect"})


@pytest.mark.parametrize("stem", STEMS)
def test_legacy_catalog_gate_rejects_missing_server_policy_before_database_connection(
    monkeypatch: pytest.MonkeyPatch, stem: str,
) -> None:
    module = _load(stem)
    monkeypatch.setenv("WM_END_USER_EMAIL", "admin@example.com")

    def unavailable(_path: str):
        raise RuntimeError("Windmill variable unavailable")

    _install_wmill(monkeypatch, unavailable)

    def unexpected_database(*args, **kwargs):
        raise AssertionError("legacy catalogue must reject before a database connection")

    monkeypatch.setattr(module.psycopg, "connect", unexpected_database)
    with pytest.raises(PermissionError, match=DENIED):
        module.main({"host": "must-not-connect"})


@pytest.mark.parametrize("stem", STEMS)
def test_legacy_catalog_gate_rejects_unlisted_identity_before_database_connection(
    monkeypatch: pytest.MonkeyPatch, stem: str,
) -> None:
    module = _load(stem)
    monkeypatch.setenv("WM_END_USER_EMAIL", "admin@example.com")
    _install_wmill(monkeypatch, lambda _path: '["other@example.com"]')

    def unexpected_database(*args, **kwargs):
        raise AssertionError("legacy catalogue must reject before a database connection")

    monkeypatch.setattr(module.psycopg, "connect", unexpected_database)
    with pytest.raises(PermissionError, match=DENIED):
        module.main({"host": "must-not-connect"})


@pytest.mark.parametrize("stem", STEMS)
def test_legacy_catalog_uses_internal_windmill_policy_not_a_caller_allowlist(stem: str) -> None:
    module = _load(stem)
    source = (BACKEND / f"{stem}.py").read_text(encoding="utf-8")
    params = inspect.signature(module.main).parameters

    assert "WM_END_USER_EMAIL" in source
    assert 'wmill.get_variable(_LEGACY_ADMIN_ALLOWLIST_PATH)' in source
    assert "research_action_writers" in source
    assert "allowlist" not in params
    assert "actor" not in params


@pytest.mark.parametrize("stem", ("get_account_library", "get_hotspot_library"))
def test_legacy_catalog_admin_preserves_empty_catalogue_contract(
    monkeypatch: pytest.MonkeyPatch, stem: str,
) -> None:
    module = _load(stem)
    monkeypatch.setenv("WM_END_USER_EMAIL", "ADMIN@example.com")
    _install_wmill(monkeypatch, lambda path: '["admin@example.com"]')
    # A controlled empty catalogue exercises the post-gate return shape without
    # accepting caller-provided policy or relying on shared database state.
    monkeypatch.setattr(module, "_connect", lambda _db: nullcontext(None))
    monkeypatch.setattr(module, "_fetch_all", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(module, "_fetch_one", lambda *_args, **_kwargs: {})

    result = module.main({"host": "not-used"})

    assert result["total"] == 0
    assert result["items"] == []
    assert result["detail"] == {}


def test_legacy_search_admin_preserves_empty_query_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load("search_research")
    monkeypatch.setenv("WM_END_USER_EMAIL", "ADMIN@example.com")
    _install_wmill(monkeypatch, lambda path: "admin@example.com")

    result = module.main({"host": "not-used"}, query="")

    assert result["total"] == 0
    assert result["items"] == []
    assert result["query"] == ""
