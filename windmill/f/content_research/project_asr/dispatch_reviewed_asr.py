# /// script
# requires-python = "==3.14.*"
# dependencies = ["douyin-research-platform @ git+https://github.com/lig9904/douyin-research-platform@e0120bb3742bf636375f22fb0139d093e4ab2de7", "psycopg[binary]==3.3.6", "wmill==1.815.0"]
# ///
"""Trusted one-submit ASR worker for a human-approved project asset.

All endpoint, credential, storage and service-identity values come from
server-side Windmill configuration. Job callers can name only persisted UUIDs.
"""
from __future__ import annotations

import hashlib
import json
from urllib.parse import urlsplit
from uuid import UUID

import psycopg
from psycopg.conninfo import make_conninfo

from douyin_research.media_storage import PrivateS3MediaStorage, S3MediaStorageConfig, StoredMediaObject
from douyin_research.project_analysis.asr_backend import ProjectASRDispatch, ProjectASRService
from douyin_research.providers.volcengine_asr import (
    VOLCENGINE_ASR_ENGINE_VERSION, VOLCENGINE_ASR_MODEL_ID, VOLCENGINE_ASR_MODEL_REVISION,
    VOLCENGINE_ASR_PROVIDER, ReviewedASRMediaDelivery, VerifiedLiveVolcengineDoubaoASRProvider,
)

_WORKER_CONFIG = "f/content_research/project_asr_worker_config"
_STORAGE_CONFIG = "f/content_research/media_storage_config"
_DATABASE_RESOURCE = "f/content_research/research_db"


def _configuration():
    import wmill
    try:
        db = wmill.get_resource(_DATABASE_RESOURCE)
        dsn = make_conninfo(host=db["host"], port=int(db.get("port", 5432)), user=db["user"],
                            password=db["password"], dbname=db["dbname"], sslmode=db.get("sslmode", "prefer"))
        worker = json.loads(wmill.get_variable(_WORKER_CONFIG))
        media = json.loads(wmill.get_variable(_STORAGE_CONFIG))
        if set(worker) != {"api_key", "service_reviewer_email"}:
            raise ValueError
        if not isinstance(worker["api_key"], str) or not worker["api_key"].strip():
            raise ValueError
        if not isinstance(worker["service_reviewer_email"], str) or "@" not in worker["service_reviewer_email"]:
            raise ValueError
        storage = PrivateS3MediaStorage(S3MediaStorageConfig(**{key: media[key] for key in (
            "endpoint", "public_endpoint", "bucket", "region", "access_key_id", "secret_access_key",
            "force_path_style", "use_ssl")}))
        return dsn, worker, media, storage
    except Exception:
        raise RuntimeError("project ASR worker configuration unavailable") from None


def main(project_id: str, video_id: str, media_review_id: str) -> dict:
    project, video, review = UUID(project_id), UUID(video_id), UUID(media_review_id)
    dsn, worker, media, storage = _configuration()
    service = ProjectASRService(dsn, delivery_origin=media["public_endpoint"],
                                trusted_worker_actor=worker["service_reviewer_email"])
    delivery: dict[str, ReviewedASRMediaDelivery] = {}

    def media_url(asset) -> str:
        # Final object integrity check occurs after the service locks and
        # rechecks the human approval, immediately before provider HTTP.
        storage.verify_object(StoredMediaObject(key=asset.object_key, sha256=asset.content_sha256,
                                                 size=asset.size_bytes, content_type=asset.content_type))
        url = storage.presigned_read_url(asset.object_key, expires_in=3600)
        query = urlsplit(url).query
        delivery["reviewed"] = ReviewedASRMediaDelivery(
            url=url, review_version=review_version,
            query_sha256=hashlib.sha256(query.encode("utf-8")).hexdigest() if query else None,
        )
        return url

    with psycopg.connect(dsn) as conn:
        row = conn.execute("""select review_version,media_fingerprint from project_asr_media_review
                            where id=%s and project_id=%s and video_id=%s""", (review, project, video)).fetchone()
    if row is None:
        raise RuntimeError("project ASR approval unavailable")
    review_version, source_fingerprint = row

    def provider():
        return VerifiedLiveVolcengineDoubaoASRProvider(
            reviewed_media_delivery=delivery["reviewed"], api_key=worker["api_key"], audio_format="wav",
            source_fingerprint=source_fingerprint, source_provider="project-private-object-storage",
            cost_currency="CNY", timeout_seconds=30,
        )

    result = service.dispatch(ProjectASRDispatch(
        project, video, review, VOLCENGINE_ASR_PROVIDER, VOLCENGINE_ASR_MODEL_ID,
        VOLCENGINE_ASR_MODEL_REVISION, VOLCENGINE_ASR_ENGINE_VERSION, source_fingerprint,
    ), media_url_factory=media_url, provider_factory=provider)
    return {**result, "cost_basis": "unknown", "worker_identity": "server_configured"}
