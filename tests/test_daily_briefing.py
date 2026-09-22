from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "windmill/f/content_research/research_dashboard.raw_app"
BACKEND = APP / "backend/get_daily_briefing.py"


def _load_backend():
    spec = importlib.util.spec_from_file_location("get_daily_briefing", BACKEND)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_daily_briefing_action_is_deterministic_and_evidence_labeled() -> None:
    module = _load_backend()

    assert module._action({"priority": 92, "l3_completed": False}) == (
        "优先人工复核",
        "规则优先级 92 分",
    )
    assert module._action({"priority": 70, "l3_completed": False}) == (
        "继续跟踪",
        "规则优先级 70 分，尚未完成 L3",
    )
    action, reason = module._action({"priority": 10, "l3_completed": True})
    assert action == "查看精研并决定归档"
    assert "L3" in reason


def test_daily_briefing_ui_exposes_drilldown_and_cost_boundaries() -> None:
    app = (APP / "App.tsx").read_text(encoding="utf-8")
    shell = (APP / "AppShell.tsx").read_text(encoding="utf-8")
    page = (APP / "DailyBriefing.tsx").read_text(encoding="utf-8")
    backend = BACKEND.read_text(encoding="utf-8")

    assert "label: '今日研判'" in shell
    assert "view: 'today', enabled: true" in shell
    assert "view === 'today'" in app
    assert "查看原始记录与完整详情" in page
    assert "supplier_daily_spend" in page
    assert "不与供应商实账重复相加" in page
    assert "metric_provenance" in backend
    assert "set transaction read only" in backend.lower()
    assert "insert into" not in backend.lower()
    assert "update " not in backend.lower()
    assert "delete from" not in backend.lower()

