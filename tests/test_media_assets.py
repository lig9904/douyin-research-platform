from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest

from douyin_research.media_assets import MediaAssetStore
from douyin_research.media_storage import PrivateS3MediaStorage, StoredMediaObject


DSN = os.getenv("TEST_DATABASE_URL")


def _object(*, sha="a" * 64, size=123, content_type="video/mp4"):
    return StoredMediaObject(PrivateS3MediaStorage.object_key(sha), size, sha, content_type)


@pytest.mark.parametrize("kwargs", [
    {"kind": "other"},
    {"storage_location": "https://secret.invalid/?token=private"},
    {"stored": _object(size=0)},
    {"stored": _object(content_type="audio/wav")},
    {"source_response_id": True},
])
def test_invalid_metadata_rejected_before_database_connection(kwargs):
    args = dict(video_id=uuid4(), kind="video", storage_location="research-media-v1",
                bucket="test-media", stored=_object())
    args.update(kwargs)
    with pytest.raises(ValueError):
        MediaAssetStore("invalid DSN must not be opened").record(**args)


@pytest.fixture
def videos():
    if not DSN:
        pytest.skip("isolated TEST_DATABASE_URL required")
    ids = [uuid4(), uuid4()]
    with psycopg.connect(DSN) as conn:
        for video_id in ids:
            conn.execute(
                "insert into source_video(id,platform,platform_video_id) values (%s,'douyin',%s)",
                (video_id, f"media-test-{video_id}"),
            )
    try:
        yield ids
    finally:
        with psycopg.connect(DSN) as conn:
            conn.execute("delete from source_video where id=any(%s)", (ids,))


def test_metadata_replay_and_shared_objects_preserve_video_binding(videos):
    store = MediaAssetStore(DSN)
    args = dict(kind="video", storage_location="research-media-v1", bucket="test-media", stored=_object())
    first = store.record(video_id=videos[0], **args)
    replay = store.record(video_id=videos[0], **args)
    other = store.record(video_id=videos[1], **args)
    assert first == replay
    assert other.id != first.id
    assert other.object_key == first.object_key
    assert store.get(videos[1], first.id) is None
    assert store.get(videos[0], first.id) == first
    assert not hasattr(first, "url")


def test_parent_asset_cannot_belong_to_another_video(videos):
    store = MediaAssetStore(DSN)
    parent = store.record(video_id=videos[0], kind="video", storage_location="research-media-v1",
                          bucket="test-media", stored=_object())
    args = dict(kind="audio", storage_location="research-media-v1", bucket="test-media",
                stored=_object(sha="b" * 64, content_type="audio/wav"), parent_asset_id=parent.id)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        store.record(video_id=videos[1], **args)
    audio = store.record(video_id=videos[0], **args)
    assert audio.parent_asset_id == parent.id


def test_replay_cannot_silently_replace_existing_metadata(videos):
    store = MediaAssetStore(DSN)
    args = dict(video_id=videos[0], kind="video", storage_location="research-media-v1", bucket="test-media")
    first = store.record(**args, stored=_object())
    with pytest.raises(ValueError, match="does not match"):
        store.record(**args, stored=_object(size=456))
    assert store.get(videos[0], first.id).size_bytes == 123
