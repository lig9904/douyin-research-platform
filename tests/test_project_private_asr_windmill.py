from pathlib import Path
import re

import pytest

from douyin_research.project_analysis.asr_backend import ProjectASRService

ROOT = Path(__file__).resolve().parents[1] / "windmill/f/content_research/project_asr"


def test_scheduled_asr_worker_identity_wins_over_injected_end_user(monkeypatch) -> None:
    monkeypatch.setenv("WM_END_USER_EMAIL", "unrelated@example.com")
    worker = ProjectASRService("postgresql://unused", delivery_origin="https://media.example.test",
                               trusted_worker_actor="service@example.com")
    assert worker._execution_actor() == "service@example.com"
    human = ProjectASRService("postgresql://unused", delivery_origin="https://media.example.test")
    assert human._execution_actor() == "unrelated@example.com"
    monkeypatch.delenv("WM_END_USER_EMAIL")
    with pytest.raises(PermissionError):
        human._execution_actor()


def test_scheduled_asr_workers_pin_the_fixed_library_in_source_and_lock() -> None:
    commits = set()
    for name in ("dispatch_reviewed_asr", "poll_reviewed_asr"):
        source = (ROOT / f"{name}.py").read_text()
        lock = (ROOT / f"{name}.script.lock").read_text()
        source_commit = re.search(r"douyin-research-platform@([0-9a-f]{40})", source)
        lock_commit = re.search(r"douyin-research-platform@([0-9a-f]{40})", lock)
        assert source_commit and lock_commit
        assert source_commit.group(1) == lock_commit.group(1)
        commits.add(source_commit.group(1))
    assert commits == {"0d087ee3d82e627f73a97607c7d2f8a010931ae0"}

def test_project_asr_review_scripts_keep_identity_resources_and_secrets_server_side() -> None:
    preview = (ROOT / "media_review_preview.py").read_text()
    approve = (ROOT / "approve_media_review.py").read_text()
    dispatch = (ROOT / "dispatch_reviewed_asr.py").read_text()
    assert "os.environ" not in approve and "actor:" not in approve  # service owns identity
    assert "get_resource(\"f/content_research/research_db\")" in preview
    assert "media_storage_config" in preview
    assert "presigned_read_url" in preview and "audio_preview_url" in preview
    assert "expected_manifest_fingerprint" in approve and "consent_statement" in approve
    poll = (ROOT / "poll_reviewed_asr.py").read_text()
    batch_poll = (ROOT / "poll_pending_reviewed_asr.py").read_text()
    assert "VerifiedLiveVolcengineDoubaoASRProvider" in dispatch
    assert "ProjectASRDispatch" in dispatch
    assert "storage.verify_object" in dispatch
    assert "trusted_worker_actor" in dispatch
    for source in (dispatch, poll):
        signature = source.split("def main(", 1)[1].split(")", 1)[0]
        assert "api_key" not in signature and "media_url" not in signature and "actor" not in signature
    assert "task_key" in poll and "provider.submit" not in poll
    assert "project_asr_execution_job" in batch_poll
    assert "_MAX_POLLS = 12" in batch_poll and "_MAX_AGE_SECONDS = 86_400" in batch_poll
    assert "run_script" in batch_poll and "media_review" not in batch_poll


def test_project_asr_schedules_only_dispatch_human_approved_and_resume_existing() -> None:
    dispatch = (ROOT / "dispatch_pending_reviewed_asr.py").read_text()
    assert "review.status='approved'" in dispatch
    assert "not exists (" in dispatch and "project_asr_execution_job" in dispatch
    assert "reviewed_at" in dispatch and "_BATCH_SIZE = 12" in dispatch
    assert "provider.submit" not in dispatch and "approve(" not in dispatch
    for name in ("dispatch_pending_reviewed_asr", "poll_pending_reviewed_asr"):
        schedule = (ROOT / f"{name}.schedule.yaml").read_text()
        assert "enabled: false" in schedule
        assert f"script_path: f/content_research/project_asr/{name}" in schedule
