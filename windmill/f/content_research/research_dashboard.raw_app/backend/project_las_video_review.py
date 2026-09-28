# /// script
# requires-python = "==3.14.*"
# dependencies = ["douyin-research-platform @ git+https://github.com/lig9904/douyin-research-platform@5b93ffa7681038ecbd355c5ffbbbb3b5cdb53180", "psycopg[binary]==3.3.6", "wmill==1.815.0"]
# ///
"""Project-private LAS video review and paid-attempt preparation.

The browser supplies only project/video/asset/review IDs and the fingerprint
shown by this same endpoint. Storage credentials and LAS account scope are
server-side Windmill variables; this script never reads the LAS API key.
"""
from __future__ import annotations

import json
import re
from typing import TypedDict
from uuid import UUID

from psycopg.conninfo import make_conninfo

from douyin_research.media_assets import MediaAssetStore
from douyin_research.media_storage import PrivateS3MediaStorage, S3MediaStorageConfig
from douyin_research.project_analysis.las import ProjectLASService, video_manifest


class postgresql(TypedDict):
    host: str
    port: int
    user: str
    password: str
    dbname: str
    sslmode: str


_CONSENT = "我已核对这份完整视频并同意交由火山 LAS 进行音画分析"
_MACHINE_CONSENT = "我授权将这份视频交由火山 LAS 进行机器音画分析；此授权不代表已完成整片人工核看"
_SCOPE = re.compile(r"[A-Za-z0-9._:-]{1,160}\Z")
_STORAGE_KEYS = (
    "endpoint", "public_endpoint", "bucket", "region", "access_key_id",
    "secret_access_key", "force_path_style", "use_ssl",
)


def _config(db: postgresql, *, needs_media: bool = True):
    dsn = make_conninfo(
        host=db["host"], port=int(db.get("port", 5432)), user=db["user"],
        password=db["password"], dbname=db["dbname"],
        sslmode=db.get("sslmode", "prefer"),
    )
    if not needs_media:
        return dsn, None, None
    import wmill

    media = json.loads(wmill.get_variable("f/content_research/media_storage_config"))
    account_scope = wmill.get_variable("f/content_research/project_las_account_scope")
    if not isinstance(account_scope, str) or not _SCOPE.fullmatch(account_scope):
        raise ValueError("LAS account scope unavailable")
    return dsn, media, account_scope


def main(
    db: postgresql, project_id: str, video_id: str, action: str,
    asset_id: str = "", review_version: str = "las-video-v2",
    expected_manifest_fingerprint: str = "", consent_statement: str = "",
    review_id: str = "", object_version_id: str = "", authorization_id: str = "",
) -> dict:
    if action not in {"list", "playback", "approve", "revoke", "prepare", "status",
                      "authorize_machine_first", "revoke_authorization",
                      "prepare_machine_first"}:
        raise ValueError("unsupported project LAS action")
    project, video = UUID(project_id), UUID(video_id)
    try:
        dsn, media, account_scope = _config(
            db, needs_media=action not in {"status", "revoke", "revoke_authorization"}
        )
        service = ProjectLASService(
            dsn, delivery_origin=(media["public_endpoint"] if media else
                                  "https://review-no-media.invalid"),
            account_scope=account_scope,
        )
        if action == "status":
            return service.status(project_id=project, video_id=video)
        if action == "list":
            return service.list_assets(project_id=project, video_id=video,
                                       review_version=review_version)
        if action == "revoke":
            return service.revoke(project_id=project, review_id=UUID(review_id))
        if action == "revoke_authorization":
            return service.revoke_machine_authorization(
                project_id=project, authorization_id=UUID(authorization_id)
            )
        if action == "prepare":
            return service.prepare(project_id=project, video_id=video,
                                   review_id=UUID(review_id))
        if action == "prepare_machine_first":
            return service.prepare_machine_first(
                project_id=project, video_id=video,
                authorization_id=UUID(authorization_id),
            )
        asset = UUID(asset_id)
        storage = PrivateS3MediaStorage(S3MediaStorageConfig(**{
            key: media[key] for key in _STORAGE_KEYS
        }))
        if action == "approve":
            if consent_statement.strip() != _CONSENT:
                raise ValueError("explicit whole-video LAS consent required")
            return service.approve(
                project_id=project, video_id=video, asset_id=asset,
                review_version=review_version,
                expected_manifest_fingerprint=expected_manifest_fingerprint,
                consent_statement=consent_statement, storage=storage,
                object_version_id=object_version_id,
            )
        if action == "authorize_machine_first":
            if consent_statement.strip() != _MACHINE_CONSENT:
                raise ValueError("explicit machine-first LAS consent required")
            return service.authorize_machine_first(
                project_id=project, video_id=video, asset_id=asset,
                expected_manifest_fingerprint=expected_manifest_fingerprint,
                consent_statement=consent_statement, storage=storage,
                object_version_id=object_version_id,
            )

        preview = service.preview(project_id=project, video_id=video,
                                  asset_id=asset, review_version=review_version,
                                  storage=storage)
        reference = MediaAssetStore(dsn).get(video, asset)
        if reference is None or video_manifest(
            reference, media["public_endpoint"],
            preview["object_version_id"] if review_version.startswith("las-video-v2") else None,
        ) != preview[
            "asset_manifest_fingerprint"
        ]:
            raise ValueError("LAS reviewed video changed since preview")
        if reference.bucket != storage.bucket or reference.storage_location != media["storage_location"]:
            raise ValueError("LAS video storage location mismatch")
        return {**preview,
                "playback_url": storage.presigned_read_url(
                    reference.object_key, expires_in=300,
                    version_id=preview["object_version_id"]),
                "expires_in_seconds": 300, "external_calls": 1}
    except PermissionError:
        raise PermissionError("PROJECT_LAS_REVIEW_ACCESS_DENIED") from None
    except Exception:
        raise RuntimeError("PROJECT_LAS_REVIEW_UNAVAILABLE_OR_STALE") from None
