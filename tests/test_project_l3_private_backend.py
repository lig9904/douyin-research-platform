"""Project-private L3 service tests; no provider HTTP is ever performed."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
from uuid import uuid4

import pytest

from douyin_research.project_analysis.l3 import (
    PROJECT_L3_EVIDENCE_VERSION,
    ProjectL3Evidence,
    ProjectL3EvidenceService,
    ProjectL3ExecutionSelection,
    ProjectL3ExecutionService,
    _fingerprint,
    _wire_safe_bundle,
)


def test_project_evidence_is_json_safe_without_changing_review_fingerprint() -> None:
    raw = {"video_metadata": {"published_at": datetime(2026, 9, 16, 11, 5, 56, tzinfo=timezone.utc)},
           "comment_features": {"like_median": Decimal("18206.5")}}
    safe = _wire_safe_bundle(raw)
    assert safe["video_metadata"]["published_at"] == "2026-09-16 11:05:56+00:00"
    assert safe["comment_features"]["like_median"] == "18206.5"
    assert json.loads(json.dumps(safe)) == safe
    assert _fingerprint(safe) == _fingerprint(raw)


def _evidence(*, project=None, video=None, transcript=None, fingerprint="a" * 64) -> ProjectL3Evidence:
    return ProjectL3Evidence(
        project_id=project or uuid4(), video_id=video or uuid4(), transcript_id=transcript or uuid4(),
        review_version="privacy-v1", fingerprint=fingerprint,
        modalities=("metadata", "comments", "transcript"),
        bundle={"evidence_version": PROJECT_L3_EVIDENCE_VERSION},
    )


def test_execution_does_not_construct_provider_when_rebuilt_evidence_is_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    initial = _evidence()
    changed = _evidence(project=initial.project_id, video=initial.video_id, transcript=initial.transcript_id, fingerprint="b" * 64)
    calls = {"prepare": 0, "provider": 0}

    def prepare(self, **kwargs):
        calls["prepare"] += 1
        return changed

    monkeypatch.setattr(ProjectL3EvidenceService, "prepare", prepare)
    selection = ProjectL3ExecutionSelection(
        project_id=initial.project_id, video_id=initial.video_id, review_id=uuid4(), transcript_id=initial.transcript_id,
        review_version=initial.review_version, evidence_fingerprint=initial.fingerprint,
        model_id="doubao-seed-1-6-251015", model_revision="r1", prompt_version="p1",
    )

    result = ProjectL3ExecutionService("postgresql://unused").run(
        selection,
        provider_factory=lambda: calls.__setitem__("provider", calls["provider"] + 1),  # type: ignore[arg-type]
    )
    assert result["status"] == "blocked"
    assert result["reason"] == "evidence_changed_since_review"
    assert calls == {"prepare": 1, "provider": 0}


def test_project_l3_source_never_mentions_global_result_or_cost_tables() -> None:
    source = __import__("inspect").getsource(__import__("douyin_research.project_analysis.l3", fromlist=["*"]))
    assert "project_l3_analysis_result" in source
    assert "project_research_task_cost" in source
    assert "from analysis_run" not in source
    assert "from research_task_cost" not in source
    assert "from transcript" not in source


def test_execution_selection_rejects_wrong_project_identity_before_a_connection() -> None:
    selection = ProjectL3ExecutionSelection(
        project_id="not-a-uuid", video_id=uuid4(), review_id=uuid4(), transcript_id=uuid4(),
        review_version="privacy-v1", evidence_fingerprint="a" * 64,
        model_id="model", model_revision="r1", prompt_version="p1",
    )
    with pytest.raises(ValueError, match="project_id"):
        ProjectL3ExecutionService("postgresql://unused").run(selection, provider_factory=lambda: None)  # type: ignore[arg-type]
