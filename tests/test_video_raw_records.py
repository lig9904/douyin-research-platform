from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "windmill/f/content_research/research_dashboard.raw_app"
BACKEND = APP / "backend/get_video_raw_records.py"


def _load_backend():
    spec = importlib.util.spec_from_file_location("get_video_raw_records", BACKEND)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_raw_payload_redacts_sensitive_keys_and_is_bounded() -> None:
    module = _load_backend()
    value = {
        "title": "公开标题",
        "access_token": "must-not-leak",
        "nested": {"password": "must-not-leak", "count": 3},
    }
    result = module._raw(value)

    assert result is not None
    parsed = json.loads(result["json_text"])
    assert parsed["title"] == "公开标题"
    assert parsed["access_token"] == "[REDACTED]"
    assert parsed["nested"]["password"] == "[REDACTED]"
    assert "must-not-leak" not in result["json_text"]
    assert result["truncated"] is False


def test_raw_record_panel_is_on_demand_and_not_part_of_mcp() -> None:
    video = (APP / "VideoLibrary.tsx").read_text(encoding="utf-8")
    panel = (APP / "src/components/RawRecordPanel.tsx").read_text(encoding="utf-8")
    backend = BACKEND.read_text(encoding="utf-8").lower()

    assert "<RawRecordPanel videoId={detail.id}" in video
    assert "只在展开时读取" in panel
    assert "MCP 不返回本区域正文" in panel
    assert "set transaction read only" in backend
    assert "insert into" not in backend
    assert "update " not in backend
    assert "delete from" not in backend
