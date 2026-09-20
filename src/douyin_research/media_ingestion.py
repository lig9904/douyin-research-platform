"""Download cached TikHub media, extract audio and persist private S3 assets.

No paid provider calls occur here. Missing/expired CDN data is a visible failure,
not an implicit paid refresh. Configuration and worker identity are supplied by
the trusted worker entrypoint, never by public form fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .l0l1.ingest import L0L1Store
from .media_assets import MediaAssetStore
from .media_processing import download_media, extract_audio, extract_tikhub_video_url
from .media_storage import PrivateS3MediaStorage, StoredMediaObject


@dataclass(frozen=True, slots=True)
class MediaIngestionConfiguration:
    storage_location: str
    bucket: str
    temp_directory: Path
    allowed_download_hosts: tuple[str, ...]
    worker_identity: str
    ffmpeg_binary: str = "ffmpeg"
    max_download_bytes: int = 2 * 1024 ** 3


class MediaIngestionService:
    def __init__(self, dsn: str, storage: PrivateS3MediaStorage,
                 config: MediaIngestionConfiguration) -> None:
        if not config.worker_identity.strip() or not config.allowed_download_hosts:
            raise ValueError("trusted worker identity and download hosts required")
        if config.bucket != storage.bucket:
            raise ValueError("metadata bucket must match configured storage")
        if not config.temp_directory.is_absolute() or not config.temp_directory.is_dir():
            raise ValueError("existing absolute media temporary directory required")
        self.dsn, self.storage, self.config = dsn, storage, config
        self.assets = MediaAssetStore(dsn)
        self.runs = L0L1Store(dsn)

    def run(self, video_id: UUID | str, *, downloader: Callable = download_media,
            extractor: Callable = extract_audio) -> dict:
        video_id = UUID(str(video_id))
        # Session-level lock survives our short metadata transactions and is
        # released even on worker failure when the DB connection closes.
        with psycopg.connect(self.dsn, autocommit=True, row_factory=dict_row) as lock:
            locked = lock.execute(
                "select pg_try_advisory_lock(hashtextextended(%s,0)) as acquired",
                (f"media-ingestion-v1:{video_id}",),
            ).fetchone()["acquired"]
            if not locked:
                return {"status": "busy", "video_id": str(video_id), "external_paid_calls": 0}
            video = lock.execute(
                "select platform,platform_video_id from source_video where id=%s", (video_id,)
            ).fetchone()
            if video is None or video["platform"] != "douyin":
                raise ValueError("media ingestion requires an existing Douyin video")
            lock.execute(
                """with interrupted as (
                  update pipeline_run r set status='failed',finished_at=now(),
                    summary=r.summary || '{"error_code":"media_worker_interrupted"}'::jsonb
                  from pipeline_run_item i
                  where r.id=i.run_id and i.entity_id=%s and i.entity_type='video'
                    and i.stage='media' and i.outcome='running'
                    and r.run_type='media_ingestion' and r.status='running'
                  returning r.id
                ) update pipeline_run_item i set outcome='failed',reason_code='media_worker_interrupted'
                  from interrupted r where i.run_id=r.id""", (video_id,),
            )
            run_id = self.runs.create_run("media_ingestion", "v1", self.config.worker_identity,
                                          platform="douyin")
            lock.execute(
                """insert into pipeline_run_item(run_id,entity_type,entity_id,stage,outcome)
                values (%s,'video',%s,'media','running')""", (run_id, video_id),
            )
            stage = "verify_existing"
            try:
                completed = self._completed_pair(lock, video_id)
                if completed:
                    for asset_id in completed:
                        asset = self.assets.get(video_id, asset_id)
                        self.storage.verify_object(StoredMediaObject(
                            asset.object_key, asset.size_bytes, asset.content_sha256, asset.content_type))
                    lock.execute(
                        """update pipeline_run_item set outcome='reused', metadata=%s
                        where run_id=%s and entity_id=%s""",
                        (Jsonb({"asset_ids": completed}), run_id, video_id),
                    )
                    self.runs.finish_run(run_id, input_count=1, output_count=2,
                        summary={"asset_ids": completed, "reused": True, "external_paid_calls": 0})
                    return {"status": "completed", "reused": True, "run_id": str(run_id),
                            "video_id": str(video_id), "asset_ids": completed, "external_paid_calls": 0}
                with TemporaryDirectory(prefix="research-media-", dir=self.config.temp_directory) as temp:
                    video_file, audio_file = Path(temp) / "video.mp4", Path(temp) / "audio.wav"
                    partial = lock.execute(
                        """select id from media_asset where video_id=%s and kind='video'
                        and storage_location=%s and bucket=%s order by created_at desc limit 1""",
                        (video_id, self.config.storage_location, self.config.bucket),
                    ).fetchone()
                    if partial:
                        stage = "video_resume"
                        video_asset = self.assets.get(video_id, partial["id"])
                        self.storage.download_file(StoredMediaObject(
                            video_asset.object_key, video_asset.size_bytes,
                            video_asset.content_sha256, video_asset.content_type), video_file)
                    else:
                        stage = "source_lookup"
                        response = lock.execute(
                            """select id,response_body from external_api_response
                            where provider='tikhub' and platform='douyin'
                              and endpoint_key='douyin.app.multi_video_v2' and response_code='200'
                              and response_body @> %s
                            order by requested_at desc,id desc limit 1""",
                            (Jsonb({"data": {"aweme_details": [{"aweme_id": video["platform_video_id"]}]}}),),
                        ).fetchone()
                        if response is None:
                            raise ValueError("cached_media_source_unavailable")
                        url = extract_tikhub_video_url(response["response_body"], video["platform_video_id"])
                        stage = "download"
                        downloaded = downloader(url, video_file,
                            allowed_hosts=self.config.allowed_download_hosts,
                            max_bytes=self.config.max_download_bytes)
                        stage = "video_upload"
                        stored_video = self.storage.upload_file(video_file, content_type="video/mp4")
                        if (downloaded.sha256, downloaded.size) != (stored_video.sha256, stored_video.size):
                            raise ValueError("media_content_changed_before_upload")
                        stage = "video_metadata"
                        video_asset = self.assets.record(video_id=video_id, kind="video",
                            storage_location=self.config.storage_location, bucket=self.config.bucket,
                            stored=stored_video, source_response_id=response["id"])
                    stage = "audio_extract"
                    audio = extractor(video_file, audio_file, ffmpeg_binary=self.config.ffmpeg_binary)
                    stage = "audio_upload"
                    stored_audio = self.storage.upload_file(audio_file, content_type="audio/wav")
                    if (audio.sha256, audio.size) != (stored_audio.sha256, stored_audio.size):
                        raise ValueError("audio_content_changed_before_upload")
                    stage = "audio_metadata"
                    audio_asset = self.assets.record(video_id=video_id, kind="audio",
                        storage_location=self.config.storage_location, bucket=self.config.bucket,
                        stored=stored_audio, source_response_id=video_asset.source_response_id, parent_asset_id=video_asset.id)
                stage = "finalize"
                asset_ids = [str(video_asset.id), str(audio_asset.id)]
                lock.execute(
                    """update pipeline_run_item set outcome='success', metadata=%s
                    where run_id=%s and entity_id=%s""",
                    (Jsonb({"asset_ids": asset_ids}), run_id, video_id),
                )
                self.runs.finish_run(run_id, input_count=1, output_count=2,
                    summary={"asset_ids": asset_ids, "external_paid_calls": 0})
                return {"status": "completed", "reused": False, "video_id": str(video_id),
                        "run_id": str(run_id), "asset_ids": asset_ids, "external_paid_calls": 0}
            except Exception:
                # Never persist exception text: transport/tool errors may hold
                # signed URLs, raw provider payloads, or local credential paths.
                error_code = f"media_{stage}_failed"
                lock.execute(
                    """update pipeline_run_item set outcome='failed', reason_code=%s
                    where run_id=%s and entity_id=%s""", (error_code, run_id, video_id),
                )
                self.runs.finish_run(run_id, status="failed", input_count=1, output_count=0,
                                      summary={"error_code": error_code, "external_paid_calls": 0})
                return {"status": "failed", "run_id": str(run_id), "video_id": str(video_id),
                        "error_code": error_code, "external_paid_calls": 0}

    def _completed_pair(self, conn, video_id: UUID) -> list[str]:
        row = conn.execute(
            """select v.id as video_asset_id,a.id as audio_asset_id
            from media_asset v join media_asset a on a.parent_asset_id=v.id and a.video_id=v.video_id
            where v.video_id=%s and v.kind='video' and a.kind='audio'
              and v.storage_location=%s and a.storage_location=v.storage_location
              and v.bucket=%s and a.bucket=v.bucket
            order by a.created_at desc limit 1""",
            (video_id, self.config.storage_location, self.config.bucket),
        ).fetchone()
        return [str(row["video_asset_id"]), str(row["audio_asset_id"])] if row else []
