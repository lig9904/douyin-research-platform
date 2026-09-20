from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path


BACKEND_PATH = (
    Path(__file__).parents[1]
    / "windmill/f/content_research/research_dashboard.raw_app/backend/get_video_library.py"
)


def _load_backend():
    spec = importlib.util.spec_from_file_location("transcript_view_backend", BACKEND_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(**overrides):
    row = {
        "text": "已审核的转写正文",
        "truncated": False,
        "quality_status": "usable",
        "asr_provider": "volcengine-doubao-asr",
        "model_id": "bigmodel",
        "model_revision": "2.0",
        "engine_version": "volc.seedasr.auc",
        "language": "zh-CN",
        "audio_duration_ms": 1200,
        "created_at": "2026-09-21T00:00:00+00:00",
        "api_cost": None,
        "asr_cost": None,
        "llm_cost": 0,
        "total_cost": None,
        "cost_currency": "CNY",
        "cost_basis": "unknown",
    }
    row.update(overrides)
    return row


def test_public_transcript_contract_preserves_unknown_cost_without_execution_internals():
    backend = _load_backend()
    transcript = backend._public_asr_transcript(_row(truncated=True))

    assert transcript == {
        "text": "已审核的转写正文",
        "truncated": True,
        "quality_status": "usable",
        "provider": "volcengine-doubao-asr",
        "model_id": "bigmodel",
        "model_revision": "2.0",
        "engine_version": "volc.seedasr.auc",
        "language": "zh-CN",
        "audio_duration_ms": 1200,
        "created_at": "2026-09-21T00:00:00+00:00",
        "cost": {
            "api_cost": None,
            "asr_cost": None,
            "llm_cost": 0,
            "total_cost": None,
            "currency": "CNY",
            "basis": "unknown",
        },
    }
    rendered = repr(transcript)
    for forbidden in (
        "metadata",
        "task_key",
        "provider_task_ref",
        "source_fingerprint",
        "media_url",
        "segments",
    ):
        assert forbidden not in transcript
        assert forbidden not in rendered


def test_backend_query_is_completed_bound_and_text_is_explicitly_limited():
    backend = _load_backend()
    source = inspect.getsource(backend)

    assert "_ASR_TRANSCRIPT_TEXT_LIMIT = 12_000" in source
    assert "from transcript t" in source
    assert "join research_task_cost c on c.id=t.task_cost_id" in source
    assert "c.status='completed'" in source
    assert "c.task_type='asr_transcription'" in source
    assert "c.video_id=t.video_id" in source
    assert "c.input_fingerprint=t.source_fingerprint" in source
    assert "left(t.text_content, %s) as text" in source
    assert "char_length(t.text_content) > %s as truncated" in source
    assert "order by t.created_at desc, t.id desc" in source
