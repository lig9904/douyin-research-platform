"""The same public video never shares private provider work across projects."""

from uuid import uuid4

import pytest

from douyin_research.project_analysis import project_asr_task_key, project_l3_task_key


def test_asr_identity_is_idempotent_only_within_exact_project_review() -> None:
    args = dict(project_id=uuid4(), video_id=uuid4(), review_id=uuid4(),
                asset_id=uuid4(), review_version="media-v1", media_fingerprint="a" * 64,
                model_id="doubao-asr", model_revision="v1", engine_version="v1")
    key = project_asr_task_key(**args)
    assert key == project_asr_task_key(**args)
    assert key != project_asr_task_key(**{**args, "project_id": uuid4()})
    assert key != project_asr_task_key(**{**args, "review_id": uuid4()})
    assert key != project_asr_task_key(**{**args, "media_fingerprint": "b" * 64})


def test_l3_identity_binds_project_evidence_and_versions() -> None:
    args = dict(project_id=uuid4(), video_id=uuid4(), review_id=uuid4(),
                review_version="privacy-v1", evidence_fingerprint="c" * 64,
                model_id="doubao", model_revision="v1", prompt_version="prompt-v1",
                schema_version="schema-v1")
    key = project_l3_task_key(**args)
    assert key == project_l3_task_key(**args)
    for changed in ({"project_id": uuid4()}, {"review_id": uuid4()},
                    {"evidence_fingerprint": "d" * 64}, {"prompt_version": "prompt-v2"}):
        assert key != project_l3_task_key(**{**args, **changed})


def test_task_identity_rejects_urls_secrets_and_malformed_hashes() -> None:
    args = dict(project_id=uuid4(), video_id=uuid4(), review_id=uuid4(),
                asset_id=uuid4(), review_version="media-v1", media_fingerprint="a" * 64,
                model_id="doubao-asr", model_revision="v1", engine_version="v1")
    with pytest.raises(ValueError, match="review_version"):
        project_asr_task_key(**{**args, "review_version": "https://private.example/audio"})
    with pytest.raises(ValueError, match="media_fingerprint"):
        project_asr_task_key(**{**args, "media_fingerprint": "not-a-hash"})
    with pytest.raises(ValueError, match="project_id"):
        project_asr_task_key(**{**args, "project_id": "not-a-project"})
