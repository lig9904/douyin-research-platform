from __future__ import annotations

from uuid import uuid4

import pytest

from douyin_research.media_assets import MediaAssetReference
from douyin_research.project_analysis.asr_backend import (
    ProjectASRService,
    ProjectASRDispatch,
    ProjectMediaReviewInput,
    _allowlist,
    _dispatch_input,
    _review_input,
    _task_key,
    asset_manifest,
)


class _ReviewerConnection:
    def __init__(self, is_project_admin: bool) -> None:
        self.is_project_admin = is_project_admin

    def execute(self, statement: str, parameters: object):
        assert "research_project_member" in statement
        assert parameters
        return self

    def fetchone(self):
        return {"exists": 1} if self.is_project_admin else None


def _asset(**changes: object) -> MediaAssetReference:
    values: dict[str, object] = {
        "id": uuid4(), "video_id": uuid4(), "kind": "audio", "storage_location": "private-s3",
        "bucket": "media", "object_key": "sha256/aa/" + "a" * 64,
        "content_sha256": "a" * 64, "size_bytes": 42, "content_type": "audio/wav",
        "source_response_id": None, "parent_asset_id": None,
    }
    values.update(changes)
    return MediaAssetReference(**values)


def test_manifest_binds_complete_asset_identity_not_only_content_hash() -> None:
    asset = _asset()
    origin = "https://media.example.test"
    assert asset_manifest(asset, origin) != asset_manifest(_asset(
        id=asset.id, video_id=asset.video_id, object_key="sha256/aa/" + "b" * 64,
    ), origin)
    assert asset_manifest(asset, origin) != asset_manifest(_asset(
        id=asset.id, video_id=asset.video_id, storage_location="other-private-store",
    ), origin)
    assert asset_manifest(asset, origin) != asset_manifest(asset, "https://other.example.test")
    with pytest.raises(ValueError, match="normalized WAV"):
        asset_manifest(_asset(content_type="audio/mp4"), origin)


def test_review_and_dispatch_inputs_reject_weak_or_secret_like_identity() -> None:
    request = ProjectMediaReviewInput(uuid4(), uuid4(), uuid4(), "review-v1", {"human": "approved"})
    _review_input(request, "a" * 64)
    with pytest.raises(ValueError, match="human review statement"):
        _review_input(ProjectMediaReviewInput(uuid4(), uuid4(), uuid4(), "v1", {}), "a" * 64)
    dispatch = ProjectASRDispatch(uuid4(), uuid4(), uuid4(), "asr", "model", "v1", "wav-v1", "a" * 64)
    _dispatch_input(dispatch)
    with pytest.raises(ValueError, match="source_fingerprint"):
        _dispatch_input(ProjectASRDispatch(uuid4(), uuid4(), uuid4(), "asr", "model", "v1", "wav-v1", "https://secret"))


def test_task_key_is_project_review_scoped_and_has_no_media_url() -> None:
    first = _task_key(uuid4(), uuid4(), uuid4(), uuid4(), "review-v1", "a" * 64, "model", "v1", "wav-v1")
    second = _task_key(uuid4(), uuid4(), uuid4(), uuid4(), "review-v1", "a" * 64, "model", "v1", "wav-v1")
    assert first != second
    assert "http" not in first and "secret" not in first


def test_global_reviewer_allowlist_is_strict_and_normalized() -> None:
    assert _allowlist('["OWNER@example.com"]') == frozenset({"owner@example.com"})
    with pytest.raises(RuntimeError, match="allowlist"):
        _allowlist('["not-an-email"]')


def test_project_owner_reviewer_does_not_require_global_variable_and_nonmember_fails_closed() -> None:
    project_id = uuid4()
    missing_variable = lambda _: (_ for _ in ()).throw(RuntimeError("404 variable not found"))
    service = ProjectASRService(
        "postgresql://example.invalid/research", delivery_origin="https://media.example.test",
        variable=missing_variable,
    )

    # A valid owner/admin approval is project-local and therefore must work
    # even when the optional global reviewer variable has not been provisioned.
    service._assert_reviewer(_ReviewerConnection(True), project_id, "owner@example.com")

    # The same missing variable must never silently authorize a non-member.
    with pytest.raises(RuntimeError, match="404 variable not found"):
        service._assert_reviewer(_ReviewerConnection(False), project_id, "outsider@example.com")

    # A configured global reviewer retains cross-project access.
    global_reviewer = ProjectASRService(
        "postgresql://example.invalid/research", delivery_origin="https://media.example.test",
        variable=lambda _: '["global-reviewer@example.com"]',
    )
    global_reviewer._assert_reviewer(
        _ReviewerConnection(False), project_id, "global-reviewer@example.com",
    )
