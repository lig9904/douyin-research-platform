"""Persisted approval checks against isolated PostgreSQL; no external calls."""
import json
import os
from uuid import uuid4

import psycopg
import pytest

from douyin_research.l2 import media_review as review
from douyin_research.media_assets import MediaAssetStore
from douyin_research.media_storage import PrivateS3MediaStorage, StoredMediaObject

DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="isolated TEST_DATABASE_URL required")


@pytest.fixture
def media(monkeypatch):
    video = uuid4()
    digest = "b" * 64
    with psycopg.connect(DSN) as conn:
        conn.execute("insert into source_video(id,platform,platform_video_id) values (%s,'douyin',%s)", (video, str(video)))
    asset = MediaAssetStore(DSN).record(video_id=video, kind="audio", storage_location="test",
        bucket="test-media", stored=StoredMediaObject(key=PrivateS3MediaStorage.object_key(digest),
            sha256=digest, size=100, content_type="audio/wav"))
    monkeypatch.setenv("WM_END_USER_EMAIL", "reviewer@example.test")
    monkeypatch.setattr(review, "_variable", lambda path: "reviewer@example.test" if path == review.REVIEWERS_PATH
                        else json.dumps({"public_endpoint": "https://media.example.test"}))
    yield asset
    with psycopg.connect(DSN) as conn:
        conn.execute("delete from source_video where id=%s", (video,))


def approve(asset, **changes):
    args = dict(video_id=asset.video_id, asset_id=asset.id, review_version="test-v1",
                expected_asset_fingerprint=review.asset_fingerprint(asset, "https://media.example.test"))
    args.update(changes)
    return review.approve_asr_media(DSN, **args)


def verify(asset, **changes):
    args = dict(video_id=asset.video_id, asset_id=asset.id, review_version="test-v1",
                source_fingerprint=asset.content_sha256,
                media_url=f"https://media.example.test/{asset.bucket}/{asset.object_key}?signature=synthetic")
    args.update(changes)
    return review.assert_reviewed_delivery(DSN, **args)


def test_missing_and_revoked_approval_are_rejected(media):
    with pytest.raises(ValueError, match="missing or stale"):
        verify(media)
    receipt = approve(media)
    assert verify(media) == receipt["asset_fingerprint"]
    assert approve(media) == receipt
    with psycopg.connect(DSN) as conn:
        conn.execute("update asr_media_review set active=false where asset_id=%s", (media.id,))
    with pytest.raises(ValueError, match="missing or stale"):
        verify(media)
    with pytest.raises(ValueError, match="conflicts"):
        approve(media)


@pytest.mark.parametrize("changes", [
    {"video_id": uuid4()}, {"review_version": "unapproved"},
    {"source_fingerprint": "c" * 64},
    {"media_url": "https://evil.example.test/test-media/object"},
    {"media_url": "https://media.example.test/test-media/another-object"},
])
def test_delivery_cannot_change_approved_video_content_or_origin(media, changes):
    approve(media)
    with pytest.raises(ValueError):
        verify(media, **changes)


def test_review_requires_authenticated_allowed_identity_and_current_preview(media, monkeypatch):
    monkeypatch.delenv("WM_END_USER_EMAIL")
    with pytest.raises(PermissionError):
        approve(media)
    monkeypatch.setenv("WM_END_USER_EMAIL", "outsider@example.test")
    with pytest.raises(PermissionError):
        approve(media)
    monkeypatch.setenv("WM_END_USER_EMAIL", "reviewer@example.test")
    with pytest.raises(ValueError, match="changed since"):
        approve(media, expected_asset_fingerprint="c" * 64)


def test_signature_rotation_preserves_review_but_metadata_change_invalidates_it(media):
    approve(media)
    assert verify(media) == verify(media,
        media_url=f"https://media.example.test/{media.bucket}/{media.object_key}?signature=rotated")
    with psycopg.connect(DSN) as conn:
        conn.execute("update media_asset set size_bytes=101 where id=%s", (media.id,))
    with pytest.raises(ValueError, match="missing or stale"):
        verify(media)
