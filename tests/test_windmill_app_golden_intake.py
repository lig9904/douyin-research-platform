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
        {}, execute=False, max_items=1, max_external_calls=1,
        enrich_details=False, force_refresh=True,
    )

    assert result["status"] == "preview"
    assert result["external_calls"] == 0
    assert result["max_items"] == 1
    assert result["retry_count"] == 0


def test_paid_run_rejects_wrong_confirmation_before_database() -> None:
    module = load_module()
    with pytest.raises(PermissionError, match="exact paid-operation confirmation"):
        module.main(
            {}, execute=True, confirmation="yes", max_items=1,
            max_external_calls=1, enrich_details=False,
        )


def test_app_surface_keeps_paid_envelope_fixed_and_server_owned() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    ui = (APP / "src/components/OperationsOverview.tsx").read_text(encoding="utf-8")
    config = (APP / "backend/run_manual_golden_intake.yaml").read_text(encoding="utf-8")
    lock = (APP / "backend/run_manual_golden_intake.lock").read_text(encoding="utf-8")

    assert 'os.environ.get("WM_END_USER_EMAIL")' in source
    assert "WM_EMAIL" not in source
    assert "_windmill_variable(API_KEY_PATH)" in source
    assert "return wmill.get_variable(path)" in source
    assert "raw_provider_payload_included" in source
    assert "API_KEY" not in ui
    assert "RUN_TIKHUB_GOLDEN_PAID" in ui
    assert "max_external_calls: 1" in ui
    assert "max_cost_usd: 0.01" in ui
    assert "enrich_details: false" in ui
    assert "$res:f/content_research/research_db" in config
    assert "wmill==1.815.0" in lock
    assert "workspace-dependencies-mode: manual" not in lock
