from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path


ROOT = Path(__file__).parents[1]
APP = ROOT / "windmill/f/content_research/research_dashboard.raw_app"
BACKEND = APP / "backend"


def _load(stem: str):
    path = BACKEND / f"{stem}.py"
    spec = importlib.util.spec_from_file_location(f"{stem}_static", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_write_backend_gets_actor_only_from_windmill_identity() -> None:
    mutate = _load("mutate_research_state")
    read_state = _load("get_research_user_state")

    assert "actor" not in inspect.signature(mutate.main).parameters
    assert "actor" not in inspect.signature(read_state.main).parameters
    source = inspect.getsource(mutate) + inspect.getsource(read_state)
    assert 'os.environ.get("WM_END_USER_EMAIL"' in source
    assert "RESEARCH_ACTION_IDENTITY_REQUIRED" in source
    assert "RESEARCH_ACTION_WRITER_FORBIDDEN" in source
    assert "max_retries" not in source
    assert "httpx" not in source


def test_dashboard_uses_real_bounded_write_backends() -> None:
    helper = (APP / "src/components/ResearchActions.ts").read_text(encoding="utf-8")
    assert "backend.mutate_research_state" in helper
    assert "backend.get_research_user_state" in helper
    assert "crypto.randomUUID" in helper

    video = (APP / "VideoLibrary.tsx").read_text(encoding="utf-8")
    account = (APP / "AccountLibrary.tsx").read_text(encoding="utf-8")
    hotspot = (APP / "HotspotLibrary.tsx").read_text(encoding="utf-8")
    combined = video + account + hotspot

    assert "action: 'save_filter'" in video
    assert "collection_name: '我的收藏'" in video
    assert "asset_type: 'video'" in video
    assert "asset_type: 'account'" in account
    assert "asset_type: 'signal'" in hotspot
    assert "写操作将在对应业务流程完成后启用" not in combined
    assert ">加入专题</Button>" not in combined  # prevents the old one-line disabled placeholders


def test_windmill_metadata_binds_database_resource_and_server_writer_policy() -> None:
    mutate_metadata = (BACKEND / "mutate_research_state.yaml").read_text(encoding="utf-8")
    assert "$res:f/content_research/research_db" in mutate_metadata
    assert "$var:f/content_research/research_action_writers" in mutate_metadata
    assert "actor:" not in mutate_metadata
    assert "secret" not in mutate_metadata.lower()

    read_metadata = (BACKEND / "get_research_user_state.yaml").read_text(encoding="utf-8")
    assert "$res:f/content_research/research_db" in read_metadata
    assert "research_action_writers" not in read_metadata
    assert "actor:" not in read_metadata
    assert "secret" not in read_metadata.lower()
