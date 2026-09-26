"""Raw App review entrypoints cannot accept a paid execution or client identity."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "windmill/f/content_research/research_dashboard.raw_app"
ASR = APP / "backend/project_asr_media_review.py"


def test_project_review_runnable_is_server_scoped_and_review_only() -> None:
    spec = importlib.util.spec_from_file_location("project_asr_media_review_contract", ASR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    parameters = inspect.signature(module.main).parameters
    assert {"project_id", "video_id", "action"} <= set(parameters)
    assert {"actor", "provider", "model_id", "api_key", "execute", "media_url"}.isdisjoint(parameters)
    source = ASR.read_text(encoding="utf-8")
    assert '"list", "playback", "approve", "revoke"' in source
    assert "ProjectASRService" in source
    assert "presigned_read_url" in source
    assert "verify_object" in source
    assert "ProjectASRDispatch" not in source
    yaml = ASR.with_suffix(".yaml").read_text(encoding="utf-8")
    assert "type: static" in yaml and "$res:f/content_research/research_db" in yaml


def test_project_review_panels_are_not_global_fallbacks() -> None:
    library = (APP / "VideoLibrary.tsx").read_text(encoding="utf-8")
    assert "projectId && canReviewProject && detail.project_inclusion_status === 'accepted' && <ProjectASRMediaReviewPanel" in library
    assert "projectId && detail.project_inclusion_status === 'accepted' && canReviewProjectL3 && detail.asr_transcript?.transcript_id" in library
    assert "canReviewProjectL3 = canReviewProject && currentProject?.can_review_l3 === true" in library
    assert "{!isProject && <ASRMediaReviewPanel" in library
    assert "{!isProject && <L3ReviewPanel" in library
    assert "backend.project_approve_l3_review" in (
        APP / "src/components/ProjectL3ReviewPanel.tsx"
    ).read_text(encoding="utf-8")
