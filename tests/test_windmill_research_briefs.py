from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path
from uuid import uuid4

import pytest
ROOT = Path(__file__).parents[1]
COLLECTORS = ROOT / "windmill/f/content_research/collectors"
FLOW = ROOT / "windmill/f/content_research/flows/research_brief_cycle.flow/flow.yaml"


def _load(stem: str):
    path = COLLECTORS / f"{stem}.py"
    spec = importlib.util.spec_from_file_location(f"{stem}_windmill_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_flow_only_accepts_brief_id_and_never_auto_runs_analysis() -> None:
    raw = FLOW.read_text()
    assert raw.count("path: f/content_research/collectors/run_research_brief") == 1
    assert raw.count("path: f/content_research/collectors/process_comment_batch") == 1
    assert raw.count("path: f/content_research/collectors/select_cycle_media") == 1
    assert "properties:\n    brief_id:" in raw
    assert "required:\n    - brief_id" in raw
    assert "run_reviewed_asr" not in raw
    assert "run_reviewed_l3" not in raw
    assert "dispatch_reviewed" not in raw
    assert "approval creation" in raw.lower()
    assert "parallel: false" in raw


def test_dispatcher_has_no_public_input_or_provider_secret() -> None:
    dispatch = _load("dispatch_due_research_briefs")
    source = inspect.getsource(dispatch)
    assert not inspect.signature(dispatch.main).parameters
    assert "limit 5" in source.lower()
    assert "run_flow_async" in source
    assert "tikhub_api_key" not in source
    assert "httpx" not in source
    schedule = (COLLECTORS / "dispatch_due_research_briefs.schedule.yaml").read_text()
    assert "enabled: false" in schedule


def test_runner_summary_rejects_any_automatic_analysis_flag() -> None:
    runner = _load("run_research_brief")
    result = {
        "run_id": str(uuid4()),
        "observations": 2,
        "unique_platform_videos": 2,
        "new_candidate_count": 1,
        "scored_videos": 2,
        "provider_call_count": 2,
        "cached_call_count": 0,
        "uncached_call_count": 2,
        "max_external_calls": 2,
        "sdk_retries": 0,
        "collect_comments": True,
        "collect_media": True,
        "review_required": True,
        "auto_submit_asr": False,
        "auto_submit_l3": False,
    }
    safe = runner._safe_result(result, uuid4())
    assert safe["external_calls"] == 2
    assert safe["raw_provider_payload_included"] is False
    result["auto_submit_l3"] = True
    with pytest.raises(RuntimeError, match="analysis boundary"):
        runner._safe_result(result, uuid4())


def test_runner_uses_fixed_server_resources_and_zero_retry_core() -> None:
    source = (COLLECTORS / "run_research_brief.py").read_text()
    assert "WM_END_USER_EMAIL" not in source
    assert 'API_KEY_PATH = "f/content_research/tikhub_api_key"' in source
    assert 'IDENTITY_PATH = "f/content_research/automation_worker_identity"' in source
    assert "max_external_calls" in source
    assert "raw_provider_payload_included" in source
