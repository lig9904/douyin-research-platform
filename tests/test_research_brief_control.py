from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
APP = ROOT / "windmill/f/content_research/research_dashboard.raw_app"
BACKEND = APP / "backend"


def _load(stem: str):
    path = BACKEND / f"{stem}.py"
    spec = importlib.util.spec_from_file_location(f"{stem}_brief_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_brief_backends_bind_actor_to_windmill_identity() -> None:
    mutate = _load("mutate_research_brief")
    read = _load("get_research_briefs")
    assert "actor" not in inspect.signature(mutate.main).parameters
    assert "actor" not in inspect.signature(read.main).parameters
    source = inspect.getsource(mutate) + inspect.getsource(read)
    assert 'os.environ.get("WM_END_USER_EMAIL"' in source
    assert "RESEARCH_ACTION_WRITER_FORBIDDEN" in source
    assert "httpx" not in source
    assert "tikhub" not in source.lower()


def test_brief_windmill_metadata_keeps_resources_server_bound() -> None:
    mutate = (BACKEND / "mutate_research_brief.yaml").read_text()
    read = (BACKEND / "get_research_briefs.yaml").read_text()
    assert "$res:f/content_research/research_db" in mutate
    assert "$var:f/content_research/research_action_writers" in mutate
    assert "actor:" not in mutate
    assert "secret" not in mutate.lower()
    assert "$res:f/content_research/research_db" in read
    assert "research_action_writers" not in read


def test_brief_configuration_is_strict_and_normalized() -> None:
    mutate = _load("mutate_research_brief")
    config = mutate._config(
        name="  神话   文旅 ", platform="douyin", source_type="keyword",
        target="  龙   传说 ", time_window_hours=72, max_items=10,
        depth="comments", cadence_hours=12,
    )
    assert config["name"] == "神话 文旅"
    assert config["target"] == "龙 传说"
    with pytest.raises(mutate.ResearchBriefError, match="bounded endpoint"):
        mutate._config(
            name="越界", platform="douyin", source_type="low_fan", target="",
            time_window_hours=720, max_items=5, depth="metadata", cadence_hours=None,
        )
    with pytest.raises(mutate.ResearchBriefError, match="configuration"):
        mutate._config(
            name="越界", platform="douyin", source_type="generic", target="all",
            time_window_hours=24, max_items=5, depth="metadata", cadence_hours=None,
        )
    with pytest.raises(mutate.ResearchBriefError, match="media scope"):
        mutate._config(
            name="媒体越界", platform="douyin", source_type="keyword", target="文旅",
            time_window_hours=24, max_items=6, depth="media", cadence_hours=None,
        )
    with pytest.raises(mutate.ResearchBriefError, match="requires subject_id"):
        mutate._config(
            name="项目研究", platform="douyin", source_type="keyword", target="文旅",
            time_window_hours=24, max_items=5, depth="metadata", cadence_hours=None,
            project_scoped=True,
        )
    subject_config = mutate._config(
        name="项目研究", platform="douyin", source_type="keyword", target="文旅",
        time_window_hours=24, max_items=5, depth="metadata", cadence_hours=None,
        project_scoped=True, subject_id="00000000-0000-4000-8000-000000000001",
    )
    assert subject_config["subject_id"] == "00000000-0000-4000-8000-000000000001"


def test_schema_records_control_plane_and_keeps_analysis_gate_separate() -> None:
    migration = (ROOT / "db/migrations/020_research_brief.sql").read_text()
    assert "create table if not exists research_brief" in migration
    assert "create table if not exists research_brief_run" in migration
    assert "source_type in ('low_fan', 'keyword', 'account')" in migration
    assert "cadence_hours is null or cadence_hours in (6, 12, 24)" in migration
    assert "ASR/L3 remain separately review-gated" in migration


def test_dashboard_exposes_briefs_as_a_first_class_research_view() -> None:
    shell = (APP / "AppShell.tsx").read_text()
    page = (APP / "ResearchBriefs.tsx").read_text()
    app = (APP / "App.tsx").read_text()
    assert "| 'briefs'" in shell
    assert "研究任务" in shell
    assert "<ResearchBriefs key={scope.mode === 'project' ? scope.projectId : 'legacy-admin'} scope={scope} onNavigate={setView} />" in app
    assert "backend.get_research_briefs" in page
    assert "backend.mutate_research_brief" in page
    assert "project_id: projectId" in page
    assert "crypto.randomUUID" in page
    assert "确认激活" in page
    assert "ASR 与 L3 始终保留独立人工审核" in page
    assert "确认归档" in page
    assert "WM_END_USER_EMAIL" not in page
    assert "tikhub_api_key" not in page
