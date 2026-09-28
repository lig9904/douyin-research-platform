from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest


ROOT = Path(__file__).parents[1]
BACKEND = ROOT / "windmill/f/content_research/research_dashboard.raw_app/backend"
WORKER_PATH = ROOT / "windmill/f/content_research/analysis/run_project_reviewed_l3.py"
DB = {"host": "localhost", "port": 5432, "user": "test", "password": "secret", "dbname": "test", "sslmode": "disable"}


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


prepare = _load(BACKEND / "project_prepare_l3_review.py", "project_prepare_l3_review")
approve = _load(BACKEND / "project_approve_l3_review.py", "project_approve_l3_review")
worker = _load(WORKER_PATH, "run_project_reviewed_l3")


def test_review_endpoints_do_not_accept_client_actor_allowlist_or_execution_fields() -> None:
    forbidden = {"actor", "provider", "model_id", "api_key", "execute", "confirmation"}
    for module in (prepare, approve):
        assert forbidden.isdisjoint(inspect.signature(module.main).parameters)
        source = inspect.getsource(module)
        assert 'WM_END_USER_EMAIL' in source
        assert 'l3_privacy_reviewers' in (module.__file__.replace('.py', '.yaml') and Path(module.__file__).with_suffix('.yaml').read_text())


def test_prepare_returns_exact_body_only_from_controlled_service(monkeypatch: pytest.MonkeyPatch) -> None:
    project, video, transcript = uuid4(), uuid4(), uuid4()
    monkeypatch.setenv("WM_END_USER_EMAIL", "owner@example.com")
    body = {"transcript": {"text": "受控审核正文"}}
    evidence = SimpleNamespace(project_id=project, video_id=video, transcript_id=transcript,
                               review_version="privacy-v1", fingerprint="a" * 64,
                               modalities=("metadata", "comments", "transcript"), bundle=body)
    class Service:
        def __init__(self, dsn): assert "secret" in dsn
        def prepare(self, **kwargs):
            assert kwargs["actor"] == "owner@example.com" and kwargs["reviewer_allowlist"] == "owner@example.com"
            return evidence
    monkeypatch.setattr(prepare, "ProjectL3ReviewService", Service)
    result = prepare.main(DB, "owner@example.com", str(project), str(video), str(transcript), "privacy-v1")
    assert result["evidence_bundle"] == body
    assert result["evidence_fingerprint"] == "a" * 64
    assert result["external_calls"] == result["llm_calls"] == 0


def test_approve_rebuilds_and_compares_the_supplied_fingerprint(monkeypatch: pytest.MonkeyPatch) -> None:
    project, video, transcript, review = uuid4(), uuid4(), uuid4(), uuid4()
    monkeypatch.setenv("WM_END_USER_EMAIL", "owner@example.com")
    evidence = SimpleNamespace(project_id=project, video_id=video, transcript_id=transcript,
                               review_version="privacy-v1", fingerprint="a" * 64)
    class Service:
        def __init__(self, dsn): pass
        def prepare(self, **kwargs): return evidence
        def approve(self, **kwargs):
            assert kwargs["evidence"] is evidence and kwargs["actor"] == "owner@example.com"
            return SimpleNamespace(review_id=review, evidence_fingerprint="a" * 64, review_version="privacy-v1", idempotent_replay=False)
    monkeypatch.setattr(approve, "ProjectL3ReviewService", Service)
    result = approve.main(DB, "owner@example.com", "approve", str(project), str(video), str(transcript), "privacy-v1", "a" * 64)
    assert result["status"] == "review_saved" and result["review_id"] == str(review)
    with pytest.raises(RuntimeError, match="PROJECT_L3_REVIEW_WRITE_FAILED"):
        approve.main(DB, "owner@example.com", "approve", str(project), str(video), str(transcript), "privacy-v1", "b" * 64)


def test_background_worker_has_only_review_id_and_fixed_server_configuration() -> None:
    assert list(inspect.signature(worker.main).parameters) == ["review_id"]
    source = WORKER_PATH.read_text()
    assert 'f/content_research/l3_worker_config' in source
    assert 'f/content_research/research_db' in source
    assert 'WM_END_USER_EMAIL' not in source
    assert 'api_key' not in inspect.signature(worker.main).parameters


def test_project_l3_schedule_selects_only_approved_unexecuted_evidence() -> None:
    batch = (WORKER_PATH.parent / "dispatch_pending_project_l3.py").read_text()
    schedule = (WORKER_PATH.parent / "dispatch_pending_project_l3.schedule.yaml").read_text()
    assert "review.status='approved'" in batch
    assert "media_review.status='approved'" in batch
    assert "not exists (" in batch and "project_l3_execution_job" in batch
    assert "approve(" not in batch and "provider.generate" not in batch
    assert "enabled: false" in schedule
