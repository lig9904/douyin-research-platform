from __future__ import annotations

import hashlib
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from douyin_research.media_ingestion import MediaIngestionConfiguration, MediaIngestionService
from douyin_research.media_processing import MediaDigest
from douyin_research.media_storage import MediaStorageError, PrivateS3MediaStorage, StoredMediaObject


DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="isolated TEST_DATABASE_URL required")


class _Storage:
    bucket = "test-media"

    def __init__(self):
        self.objects = {}
        self.bodies = {}
        self.uploads = 0

    def upload_file(self, path, *, content_type):
        content = Path(path).read_bytes()
        sha = hashlib.sha256(content).hexdigest()
        obj = StoredMediaObject(PrivateS3MediaStorage.object_key(sha), len(content), sha, content_type)
        self.objects[obj.key] = obj
        self.bodies[obj.key] = content
        self.uploads += 1
        return obj

    def verify_object(self, obj):
        if self.objects.get(obj.key) != obj:
            raise MediaStorageError("S3 media object is missing")

    def download_file(self, obj, destination):
        self.verify_object(obj)
        destination.write_bytes(self.bodies[obj.key])


@pytest.fixture
def source(tmp_path):
    video_id = uuid4()
    platform_id = f"synthetic-media-{video_id}"
    worker = f"test-media-worker-{video_id}"
    payload = {"data": {"aweme_details": [{"aweme_id": platform_id, "video": {
        "play_addr": {"url_list": ["https://cdn.example.test/video?token=private-token"]}
    }}]}}
    with psycopg.connect(DSN) as conn:
        conn.execute("insert into source_video(id,platform,platform_video_id) values (%s,'douyin',%s)",
                     (video_id, platform_id))
        response_id = conn.execute(
            """insert into external_api_response(provider,platform,endpoint_key,response_code,response_body)
            values ('tikhub','douyin','douyin.app.multi_video_v2','200',%s) returning id""",
            (Jsonb(payload),),
        ).fetchone()[0]
    storage = _Storage()
    config = MediaIngestionConfiguration("research-media-v1", storage.bucket, tmp_path,
                                         ("cdn.example.test",), worker)
    service = MediaIngestionService(DSN, storage, config)
    try:
        yield service, storage, video_id, response_id
    finally:
        with psycopg.connect(DSN) as conn:
            conn.execute("delete from pipeline_run where triggered_by=%s", (worker,))
            conn.execute("delete from source_video where id=%s", (video_id,))
            conn.execute("delete from external_api_response where id=%s", (response_id,))


def _write(path, content):
    path.write_bytes(content)
    return MediaDigest(hashlib.sha256(content).hexdigest(), len(content))


def _download(url, destination, **kwargs):
    assert url.startswith("https://cdn.example.test/")
    return _write(destination, b"synthetic-video")


def _extract(input, output, **kwargs):
    assert input.read_bytes() == b"synthetic-video"
    return _write(output, b"synthetic-wav")


def test_media_chain_persists_bound_assets_and_reuses_without_downloading(source):
    service, storage, video_id, response_id = source
    first = service.run(video_id, downloader=_download, extractor=_extract)
    assert first["status"] == "completed"
    assert first["external_paid_calls"] == 0
    assert len(first["asset_ids"]) == 2
    assert list(service.config.temp_directory.iterdir()) == []

    def forbidden(*args, **kwargs):
        pytest.fail("completed media must not be downloaded or extracted again")

    replay = service.run(video_id, downloader=forbidden, extractor=forbidden)
    assert replay["reused"] is True
    assert replay["asset_ids"] == first["asset_ids"]
    assert storage.uploads == 2
    video, audio = [service.assets.get(video_id, asset_id) for asset_id in first["asset_ids"]]
    assert audio.parent_asset_id == video.id
    assert video.source_response_id == audio.source_response_id == response_id
    assert "private-token" not in repr(first)


def test_one_video_detail_can_supply_exact_video_without_paid_refresh(source):
    service, storage, video_id, response_id = source
    with psycopg.connect(DSN) as conn:
        platform_id = conn.execute(
            "select platform_video_id from source_video where id=%s", (video_id,)
        ).fetchone()[0]
        conn.execute(
            """update external_api_response
               set endpoint_key='douyin.app.one_video', response_body=%s
               where id=%s""",
            (Jsonb({"data": {"aweme_detail": {"aweme_id": platform_id,
                "video": {"play_addr": {"url_list": ["https://cdn.example.test/video?token=private-token"]}}}}}),
             response_id),
        )
    result = service.run(video_id, downloader=_download, extractor=_extract)
    assert result["status"] == "completed"
    assert result["external_paid_calls"] == 0
    assert storage.uploads == 2
    assert service.assets.get(video_id, result["asset_ids"][0]).source_response_id == response_id


def test_one_video_detail_for_other_video_is_not_a_media_source(source):
    service, storage, video_id, response_id = source
    with psycopg.connect(DSN) as conn:
        conn.execute(
            """update external_api_response
               set endpoint_key='douyin.app.one_video', response_body=%s
               where id=%s""",
            (Jsonb({"data": {"aweme_detail": {"aweme_id": "other-video",
                "video": {"play_addr": {"url_list": ["https://cdn.example.test/other"]}}}}}),
             response_id),
        )
    result = service.run(video_id, downloader=_download, extractor=_extract)
    assert result["status"] == "failed"
    assert result["error_code"] == "media_source_lookup_failed"
    assert result["external_paid_calls"] == 0
    assert storage.uploads == 0


def test_newest_exact_detail_without_media_does_not_use_older_url(source):
    service, storage, video_id, _ = source
    with psycopg.connect(DSN) as conn:
        platform_id = conn.execute(
            "select platform_video_id from source_video where id=%s", (video_id,)
        ).fetchone()[0]
        newer_id = conn.execute(
            """insert into external_api_response(
                   provider,platform,endpoint_key,response_code,response_body,requested_at)
               values ('tikhub','douyin','douyin.app.one_video','200',%s,
                       now() + interval '1 minute') returning id""",
            (Jsonb({"data": {"aweme_detail": {"aweme_id": platform_id}}}),),
        ).fetchone()[0]
    try:
        result = service.run(video_id, downloader=_download, extractor=_extract)
        assert result["status"] == "failed"
        assert result["error_code"] == "media_source_lookup_failed"
        assert result["external_paid_calls"] == 0
        assert storage.uploads == 0
    finally:
        with psycopg.connect(DSN) as conn:
            conn.execute("delete from external_api_response where id=%s", (newer_id,))


def test_equal_timestamp_uses_newer_exact_detail_id(source):
    service, _, video_id, older_id = source
    with psycopg.connect(DSN) as conn:
        platform_id = conn.execute(
            "select platform_video_id from source_video where id=%s", (video_id,)
        ).fetchone()[0]
        newer_id = conn.execute(
            """insert into external_api_response(
                   provider,platform,endpoint_key,response_code,response_body,requested_at)
               select 'tikhub','douyin','douyin.app.one_video','200',%s,requested_at
               from external_api_response where id=%s returning id""",
            (Jsonb({"data": {"aweme_detail": {"aweme_id": platform_id,
                "video": {"play_addr": {"url_list": ["https://cdn.example.test/newer"]}}}}}),
             older_id),
        ).fetchone()[0]
    try:
        result = service.run(video_id, downloader=_download, extractor=_extract)
        assert result["status"] == "completed"
        assert service.assets.get(video_id, result["asset_ids"][0]).source_response_id == newer_id
    finally:
        with psycopg.connect(DSN) as conn:
            conn.execute("delete from external_api_response where id=%s", (newer_id,))


def test_extraction_failure_is_persisted_and_temp_files_cleaned(source):
    service, storage, video_id, _ = source

    def failed(*args, **kwargs):
        raise RuntimeError("https://private.invalid/?token=private-token")

    result = service.run(video_id, downloader=_download, extractor=failed)
    assert result["status"] == "failed"
    assert result["error_code"] == "media_audio_extract_failed"
    assert list(service.config.temp_directory.iterdir()) == []
    with psycopg.connect(DSN) as conn:
        status, summary = conn.execute("select status,summary from pipeline_run where id=%s",
                                       (result["run_id"],)).fetchone()
        assert status == "failed" and "private-token" not in repr(summary)
        assert conn.execute("select count(*) from media_asset where video_id=%s and kind='audio'",
                            (video_id,)).fetchone()[0] == 0
    def no_cdn_retry(*args, **kwargs):
        pytest.fail("partial video must resume from S3, not repeat CDN download")

    retry = service.run(video_id, downloader=no_cdn_retry, extractor=_extract)
    assert retry["status"] == "completed"
    assert storage.uploads == 2


def test_deleted_storage_object_does_not_report_reused_success(source):
    service, storage, video_id, _ = source
    assert service.run(video_id, downloader=_download, extractor=_extract)["status"] == "completed"
    storage.objects.clear()
    assert service.run(video_id, downloader=_download, extractor=_extract)["status"] == "failed"


def test_missing_cached_source_does_not_trigger_paid_refresh(source):
    service, storage, video_id, response_id = source
    with psycopg.connect(DSN) as conn:
        conn.execute("delete from external_api_response where id=%s", (response_id,))
    result = service.run(video_id, downloader=_download, extractor=_extract)
    assert result["status"] == "failed"
    assert result["external_paid_calls"] == 0
    assert storage.uploads == 0


def test_concurrent_worker_returns_busy_without_network(source):
    service, storage, video_id, _ = source
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("select pg_advisory_lock(hashtextextended(%s,0))", (f"media-ingestion-v1:{video_id}",))
        assert service.run(video_id)["status"] == "busy"
    assert storage.uploads == 0


def test_real_ffmpeg_audio_conversion_is_persisted_without_external_services(source):
    import shutil
    import wave

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg executable required for actual conversion")
    service, storage, video_id, _ = source

    def local_audio_fixture(_url, destination, **_kwargs):
        # Explicitly synthetic stereo source, not real-video acceptance.
        with wave.open(str(destination), "wb") as stream:
            stream.setnchannels(2)
            stream.setsampwidth(2)
            stream.setframerate(44100)
            stream.writeframes(b"\0" * 17640)
        content = destination.read_bytes()
        return MediaDigest(hashlib.sha256(content).hexdigest(), len(content))

    from douyin_research.media_processing import extract_audio

    def verify_conversion(input, output, **kwargs):
        result = extract_audio(input, output, **kwargs)
        with wave.open(str(output), "rb") as stream:
            assert (stream.getnchannels(), stream.getframerate(), stream.getsampwidth()) == (1, 16000, 2)
            assert stream.getnframes() > 0
        return result

    result = service.run(video_id, downloader=local_audio_fixture, extractor=verify_conversion)
    assert result["status"] == "completed"
    assert storage.uploads == 2


def test_restart_marks_abandoned_attempt_failed_then_resumes(source):
    service, _, video_id, _ = source
    interrupted_id = service.runs.create_run("media_ingestion", "v1", service.config.worker_identity,
                                             platform="douyin")
    with psycopg.connect(DSN) as conn:
        conn.execute("""insert into pipeline_run_item(run_id,entity_type,entity_id,stage,outcome)
                     values (%s,'video',%s,'media','running')""", (interrupted_id, video_id))
    assert service.run(video_id, downloader=_download, extractor=_extract)["status"] == "completed"
    with psycopg.connect(DSN) as conn:
        assert conn.execute("select status from pipeline_run where id=%s", (interrupted_id,)).fetchone()[0] == "failed"
        assert conn.execute("select reason_code from pipeline_run_item where run_id=%s", (interrupted_id,)).fetchone()[0] == "media_worker_interrupted"
