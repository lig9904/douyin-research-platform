from __future__ import annotations

import importlib.util
import sys
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


def test_authenticated_app_dispatches_only_to_the_bounded_collector(monkeypatch) -> None:
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
        "_run_child",
        lambda args: calls.append(("script", args))
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
    assert calls[1][1]["db"] == module.DB_RESOURCE
    assert calls[1][1]["max_external_calls"] == 1


def test_app_surface_keeps_paid_envelope_fixed_and_server_owned() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    ui = (APP / "src/components/OperationsOverview.tsx").read_text(encoding="utf-8")
    lock = (APP / "backend/run_manual_golden_intake.lock").read_text(encoding="utf-8")
    workspace_lock = (ROOT / "windmill/wmill-lock.yaml").read_text(encoding="utf-8")

    assert 'os.environ.get("WM_END_USER_EMAIL")' in source
    assert "WM_EMAIL" not in source
    assert "_get_variable(WRITER_ALLOWLIST_PATH)" in source
    assert '"parent_job"' in source
    assert "jobs/run/p/" in source
    assert "jobs_u/completed/get_result_maybe/" in source
    assert 'DB_RESOURCE = "$res:f/content_research/research_db"' in source
    assert "API_KEY" not in source
    assert "API_KEY" not in ui
    assert "RUN_TIKHUB_GOLDEN_PAID" in ui
    assert "max_external_calls: 2" in ui
    assert "max_cost_usd: null" in ui
    assert "enrich_details: true" in ui
    assert "无固定金额上限" in ui
    assert lock.strip() == "# py: 3.12"
    assert "workspace-dependencies-mode: manual" not in lock
    assert (
        "f/content_research/research_dashboard.raw_app+"
        "run_manual_golden_intake.py:"
    ) in workspace_lock
