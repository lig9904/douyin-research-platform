# /// script
# requires-python = "==3.14.*"
# dependencies = [
#   "douyin-research-platform @ git+https://github.com/lig9904/douyin-research-platform@5b93ffa7681038ecbd355c5ffbbbb3b5cdb53180",
#   "psycopg[binary]==3.3.6", "wmill==1.815.0",
# ]
# ///

"""Short-lived private video playback, scoped to one project's own evidence.

Public cross-project shares never grant access to a cached media object.
"""

from __future__ import annotations

import json
import os
import re
from typing import TypedDict
from urllib.parse import urlsplit
from uuid import UUID

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from douyin_research.media_assets import MediaAssetReference
from douyin_research.media_storage import (
    PrivateS3MediaStorage, S3MediaStorageConfig, StoredMediaObject,
)


class postgresql(TypedDict):
    host: str
    port: int
    user: str
    password: str
    dbname: str
    sslmode: str


_EMAIL = re.compile(r"^[^\s@]{1,128}@[^\s@]{1,120}$")
_CONFIG_PATH = "f/content_research/media_storage_config"
_CONFIG_FIELDS = (
    "endpoint", "public_endpoint", "bucket", "region", "access_key_id",
    "secret_access_key", "force_path_style", "use_ssl",
)
_PREVIEW_SECONDS = 300
_UNAVAILABLE = "PROJECT_VIDEO_PREVIEW_UNAVAILABLE"


def _actor() -> str:
    actor = os.environ.get("WM_END_USER_EMAIL", "").strip().lower()
    if len(actor) > 254 or not _EMAIL.fullmatch(actor):
        raise PermissionError(_UNAVAILABLE)
    return actor


def _id(value: str) -> UUID:
    if not isinstance(value, str):
        raise ValueError("invalid ID")
    return UUID(value)


def _dsn(db: postgresql) -> str:
    return make_conninfo(
        host=db["host"], port=int(db.get("port", 5432)), user=db["user"],
        password=db["password"], dbname=db["dbname"],
        sslmode=db.get("sslmode", "prefer"),
    )


def _asset(dsn: str, project: UUID, video: UUID, actor: str) -> MediaAssetReference | None:
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("set transaction isolation level repeatable read read only")
        allowed = conn.execute(
            """select 1 from research_project project_row
               join research_organization organization_row
                 on organization_row.id=project_row.organization_id
               join project_video_inclusion inclusion_row
                 on inclusion_row.project_id=project_row.id
               join source_video video_row on video_row.id=inclusion_row.video_id
               where project_row.id=%s and video_row.id=%s
                 and project_row.status='active' and organization_row.status='active'
                 and project_actor_can_read(project_row.id,%s)
                 and inclusion_row.status in ('candidate','shortlisted','accepted')
                 and video_row.platform='douyin'
                 and video_row.availability_status='available'""",
            (project, video, actor),
        ).fetchone()
        if allowed is None:
            raise PermissionError(_UNAVAILABLE)
        rows = conn.execute(
            """select id,video_id,kind,storage_location,bucket,object_key,
                      content_sha256,size_bytes,content_type,source_response_id,parent_asset_id
               from media_asset where video_id=%s and kind='video'
               order by id limit 2""",
            (video,),
        ).fetchall()
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError("ambiguous video asset")
    reference = MediaAssetReference(**rows[0])
    if reference.video_id != video or reference.kind != "video":
        raise ValueError("video binding invalid")
    return reference


def _stored(asset: MediaAssetReference) -> StoredMediaObject:
    digest = asset.content_sha256
    if (
        not isinstance(digest, str) or digest != digest.lower()
        or asset.object_key != PrivateS3MediaStorage.object_key(digest)
        or type(asset.size_bytes) is not int or asset.size_bytes <= 0
        or not isinstance(asset.content_type, str)
        or not asset.content_type.startswith("video/")
    ):
        raise ValueError("video metadata invalid")
    return StoredMediaObject(
        key=asset.object_key, sha256=digest, size=asset.size_bytes,
        content_type=asset.content_type,
    )


def _storage(asset: MediaAssetReference) -> PrivateS3MediaStorage:
    import wmill

    config = json.loads(wmill.get_variable(_CONFIG_PATH))
    if not isinstance(config, dict):
        raise ValueError("storage config invalid")
    storage = PrivateS3MediaStorage(S3MediaStorageConfig(**{
        key: config[key] for key in _CONFIG_FIELDS
    }))
    if storage.bucket != asset.bucket or config["storage_location"] != asset.storage_location:
        raise ValueError("storage location mismatch")
    return storage


def _https_url(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("playback URL invalid")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https" or not parsed.netloc
        or parsed.username is not None or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("playback URL invalid")
    return value


def main(db: postgresql, project_id: str, video_id: str) -> dict:
    """Return a verified 300-second URL without exposing storage metadata."""
    try:
        actor = _actor()
        project, video = _id(project_id), _id(video_id)
        reference = _asset(_dsn(db), project, video, actor)
        if reference is None:
            return {"has_media": False}
        if reference.video_id != video or reference.kind != "video":
            raise ValueError("video binding invalid")
        stored = _stored(reference)
        storage = _storage(reference)
        storage.verify_object(stored)
        return {
            "has_media": True,
            "playback_url": _https_url(storage.presigned_read_url(
                reference.object_key, expires_in=_PREVIEW_SECONDS,
            )),
            "expires_in_seconds": _PREVIEW_SECONDS,
        }
    except Exception:
        # No distinction between missing, unauthorized, altered, or broken
        # objects; transport errors may contain signed URLs or credentials.
        raise RuntimeError(_UNAVAILABLE) from None
