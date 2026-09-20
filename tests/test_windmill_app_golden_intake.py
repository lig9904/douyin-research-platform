from __future__ import annotations

import importlib.util
import sys
import tomllib
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
APP = ROOT / "windmill/f/content_research/research_dashboard.raw_app"
SCRIPT = APP / "backend/run_manual_golden_intake.py"


def load_module():
    spec = importlib.util.spec_from_file_location("app_manual_golden_intake", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_app_dependencies_are_declared_in_runtime_supported_pep723():
    source = SCRIPT.read_text()
    block = source.split("# /// script\n", 1)[1].split("# ///", 1)[0]
    metadata = tomllib.loads("\n".join(line.removeprefix("# ") for line in block.splitlines()))
    assert metadata["requires-python"] == "==3.12.*"
    assert "psycopg[binary]==3.3.6" in metadata["dependencies"]
    assert any(dep.startswith("douyin-research-platform @ git+https://github.com/lig9904/")
               for dep in metadata["dependencies"])


def test_preview_is_zero_call_and_does_not_require_database() -> None:
    module = load_module()
    result = module.main(
        execute=False, force_refresh=True,
    )

    assert result["status"] == "preview"
    assert result["external_calls"] == 0
    assert result["max_items"] == 5
    assert result["max_external_calls"] == 2
    assert result["max_cost_usd"] is None
    assert result["retry_count"] == 0


def test_paid_run_rejects_wrong_confirmation_before_database() -> None:
    module = load_module()
    with pytest.raises(PermissionError, match="exact paid-operation confirmation"):
        module.main(
            execute=True, confirmation="yes", max_items=1,
            max_external_calls=1, enrich_details=False,
        )


def test_authenticated_app_runs_intake_with_its_own_viewer(monkeypatch) -> None:
    module = load_module()
    calls = []

    monkeypatch.setenv("WM_END_USER_EMAIL", "operator@example.com")
    monkeypatch.setattr(
        module,
        "_get_variable",
        lambda path: calls.append(("variable", path)) or "operator@example.com",
    )
    monkeypatch.setattr(
        module,
        "_run_intake",
        lambda args, actor: calls.append(("intake", args, actor))
        or {"status": "completed", "provider_call_count": 1},
    )
    result = module.main(
        execute=True,
        confirmation=module.CONFIRMATION,
        max_items=1,
        max_external_calls=1,
        max_cost_usd=None,
        enrich_details=False,
    )

    assert result["status"] == "completed"
    assert calls[0] == ("variable", module.WRITER_ALLOWLIST_PATH)
    assert calls[1][2] == "operator@example.com"
    assert calls[1][1]["max_external_calls"] == 1


@pytest.mark.parametrize("viewer", ["", "viewer@example.com"])
def test_missing_or_disallowed_viewer_never_runs_intake(monkeypatch, viewer):
    module = load_module()
    monkeypatch.setenv("WM_END_USER_EMAIL", viewer)
    monkeypatch.setenv("WM_EMAIL", "operator@example.com")
    monkeypatch.setattr(module, "_get_variable", lambda _: "operator@example.com")
    monkeypatch.setattr(module, "_run_intake", lambda *_: pytest.fail("unauthorized intake"))
    with pytest.raises(PermissionError):
        module.main(execute=True, confirmation=module.CONFIRMATION)


@pytest.mark.parametrize("locked", [True, False])
def test_direct_intake_keeps_lock_actor_and_sanitized_result(monkeypatch, locked):
    import psycopg
    from douyin_research.l0l1 import real_data

    module = load_module()
    calls = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def cursor(self):
            return self

        def execute(self, sql, params):
            calls.append(sql)

        def fetchone(self):
            return (locked,)

    monkeypatch.setenv("WM_WORKSPACE", "test-workspace")
    monkeypatch.setattr(psycopg, "connect", lambda _: Connection())
    monkeypatch.setattr(module, "_api", lambda method, path: {"host": "db", "dbname": "research"})
    monkeypatch.setattr(module, "_get_variable", lambda _: calls.append("secret") or "secret-value")

    def run_live(**kwargs):
        calls.append("provider")
        assert kwargs["triggered_by"] == "operator@example.com"
        assert kwargs["plan"].max_cost_usd is None
        assert kwargs["plan"].retry_count == 0
        return {**dict.fromkeys(("source_count", "observations", "unique_platform_videos",
                "scored_videos", "max_external_calls", "provider_call_count",
                "cached_call_count", "uncached_call_count", "retry_count"), 0),
                "run_id": "private-id", "payload": "private-payload"}

    monkeypatch.setattr(real_data, "run_live", run_live)
    args = module.main(execute=False)
    args.pop("status")
    args.pop("external_calls")
    args.pop("retry_count")
    if not locked:
        with pytest.raises(RuntimeError, match="already running"):
            module._run_intake(args, "operator@example.com")
        assert "secret" not in calls
        assert "provider" not in calls
    else:
        result = module._run_intake(args, "operator@example.com")
        assert result["status"] == "completed"
        assert "private" not in repr(result)
        assert "secret-value" not in repr(result)
        assert calls[-1].startswith("select pg_advisory_unlock")


def test_app_surface_keeps_paid_envelope_fixed_and_server_owned() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    ui = (APP / "src/components/OperationsOverview.tsx").read_text(encoding="utf-8")
    lock = (APP / "backend/run_manual_golden_intake.lock").read_text(encoding="utf-8")
    workspace_lock = (ROOT / "windmill/wmill-lock.yaml").read_text(encoding="utf-8")

    assert 'os.environ.get("WM_END_USER_EMAIL")' in source
    assert "WM_EMAIL" not in source
    assert "_get_variable(WRITER_ALLOWLIST_PATH)" in source
    assert "jobs/run/p/" not in source
    assert "pg_try_advisory_lock" in source
    assert 'DB_RESOURCE = "$res:f/content_research/research_db"' in source
    assert 'API_KEY_PATH = "f/content_research/tikhub_api_key"' in source
    assert "API_KEY" not in ui
    assert "RUN_TIKHUB_GOLDEN_PAID" in ui
    assert "max_external_calls: 2" in ui
    assert "max_cost_usd: null" in ui
    assert "enrich_details: true" in ui
    assert "无固定金额上限" in ui
    assert "# py: 3.12" in lock
    assert "douyin-research-platform @ git+https://" in lock
    assert "psycopg-binary==3.3.6" in lock
    assert (
        "f/content_research/research_dashboard.raw_app+"
        "run_manual_golden_intake.py:"
    ) in workspace_lock
