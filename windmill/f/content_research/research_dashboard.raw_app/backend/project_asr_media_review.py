# /// script
# requires-python = "==3.14.*"
# dependencies = ["douyin-research-platform @ git+https://github.com/lig9904/douyin-research-platform@34ce0706b2b937c04bac732413d5809ac271ed9c", "psycopg[binary]==3.3.6", "wmill==1.815.0"]
# ///
"""Project-private audio review; never submits an ASR task."""
from __future__ import annotations

import json
from typing import TypedDict
from uuid import UUID

from psycopg.conninfo import make_conninfo

from douyin_research.media_assets import MediaAssetStore
from douyin_research.media_storage import PrivateS3MediaStorage, S3MediaStorageConfig, StoredMediaObject
from douyin_research.project_analysis.asr_backend import (
    ProjectASRService, ProjectMediaReviewInput, asset_manifest,
)


class postgresql(TypedDict):
    host: str
    port: int
    user: str
    password: str
    dbname: str
    sslmode: str


def _config(db: postgresql):
    import wmill

    dsn = make_conninfo(
        host=db["host"], port=int(db.get("port", 5432)), user=db["user"],
        password=db["password"], dbname=db["dbname"],
        sslmode=db.get("sslmode", "prefer"),
    )
    config = json.loads(wmill.get_variable("f/content_research/media_storage_config"))
    origin = config["public_endpoint"]
    return dsn, origin, config


def main(
    db: postgresql, project_id: str, video_id: str, action: str,
    asset_id: str = "", review_version: str = "media-v1",
    expected_manifest_fingerprint: str = "", consent_statement: str = "",
    media_review_id: str = "",
) -> dict:
    if action not in {"list", "playback", "approve", "revoke"}:
        raise ValueError("unsupported project audio review action")
    project, video = UUID(project_id), UUID(video_id)
    try:
        dsn, origin, config = _config(db)
        service = ProjectASRService(dsn, delivery_origin=origin)
        if action == "list":
            return service.list_assets(project_id=project, video_id=video,
                                       review_version=review_version)
        if action == "revoke":
            return service.revoke(project_id=project, media_review_id=UUID(media_review_id))
        asset = UUID(asset_id)
        request = ProjectMediaReviewInput(
            project, video, asset, review_version,
            {"consent_statement": consent_statement.strip()},
        )
        if action == "approve":
            if consent_statement.strip() != "我已核对音频内容并同意交由云端转写":
                raise ValueError("explicit audio consent statement is required")
            return service.approve(request, expected_manifest_fingerprint=expected_manifest_fingerprint)

        # Authorization and the exact manifest check precede object-store
        # verification and signed URL creation. Never return object keys.
        preview = service.preview(request)
        reference = MediaAssetStore(dsn).get(video, asset)
        if reference is None or asset_manifest(reference, origin) != preview["asset_manifest_fingerprint"]:
            raise ValueError("project audio changed since preview")
        storage = PrivateS3MediaStorage(S3MediaStorageConfig(**{key: config[key] for key in (
            "endpoint", "public_endpoint", "bucket", "region", "access_key_id",
            "secret_access_key", "force_path_style", "use_ssl",
        )}))
        if storage.bucket != reference.bucket or config["storage_location"] != reference.storage_location:
            raise ValueError("project audio storage location mismatch")
        storage.verify_object(StoredMediaObject(
            key=reference.object_key, sha256=reference.content_sha256,
            size=reference.size_bytes, content_type=reference.content_type,
        ))
        return {**preview, "delivery_origin": origin,
                "playback_url": storage.presigned_read_url(reference.object_key, expires_in=300),
                "expires_in_seconds": 300, "external_calls": 1}
    except PermissionError:
        raise PermissionError("PROJECT_ASR_REVIEW_ACCESS_DENIED") from None
    except Exception:
        raise RuntimeError("PROJECT_ASR_REVIEW_UNAVAILABLE_OR_STALE") from None
