# /// script
# requires-python = "==3.13.*"
# dependencies = [
#   "douyin-research-platform @ git+https://github.com/lig9904/douyin-research-platform@92153f603368a3ab2cb7810924d6d3948857987a",
#   "psycopg[binary]==3.3.6", "wmill==1.815.0",
# ]
# ///

"""Authenticated, short-lived playback for one confirmed private video object."""

from __future__ import annotations

import json
import os
from urllib.parse import urlsplit
from uuid import UUID

import psycopg
from psycopg.conninfo import make_conninfo

from douyin_research.l3.review import authorize_reviewer
from douyin_research.media_assets import MediaAssetStore
from douyin_research.media_storage import (
    PrivateS3MediaStorage,
    S3MediaStorageConfig,
    StoredMediaObject,
)


_REVIEWERS_PATH = "f/content_research/l3_privacy_reviewers"
_STORAGE_CONFIG_PATH = "f/content_research/media_storage_config"
_STORAGE_FIELDS = (
    "endpoint", "public_endpoint", "bucket", "region", "access_key_id",
    "secret_access_key", "force_path_style", "use_ssl",
)
_PREVIEW_SECONDS = 300


def _authorize() -> None:
    """Use the same end-user review boundary as the ASR media review panel."""
    import wmill

    authorize_reviewer(
        os.environ.get("WM_END_USER_EMAIL"),
        wmill.get_variable(_REVIEWERS_PATH),
    )


def _dsn() -> str:
    import wmill

    try:
        db = wmill.get_resource("f/content_research/research_db")
        return make_conninfo(
            host=db["host"],
            port=db.get("port", 5432),
            user=db["user"],
            password=db["password"],
            dbname=db["dbname"],
            sslmode=db.get("sslmode", "prefer"),
        )
    except Exception:
        raise RuntimeError("video preview database configuration unavailable") from None


def _canonical_object(asset) -> StoredMediaObject:
    """Recheck immutable metadata before any storage credential is loaded."""
    sha256 = asset.content_sha256
    if (
        asset.kind != "video"
        or not isinstance(sha256, str)
        or sha256 != sha256.lower()
        or asset.object_key != PrivateS3MediaStorage.object_key(sha256)
        or type(asset.size_bytes) is not int
        or asset.size_bytes <= 0
        or not isinstance(asset.content_type, str)
        or not asset.content_type.startswith("video/")
    ):
        raise ValueError("invalid confirmed video media metadata")
    return StoredMediaObject(
        key=asset.object_key,
        sha256=sha256,
        size=asset.size_bytes,
        content_type=asset.content_type,
    )


def _load_single_video_asset(dsn: str, video_id: UUID):
    """Select at most two IDs so a non-unique preview is never chosen silently."""
    with psycopg.connect(dsn) as conn:
        conn.execute("set transaction read only")
        rows = conn.execute(
            """select id from media_asset
               where video_id=%s and kind='video'
               order by id limit 2""",
            (video_id,),
        ).fetchall()
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError("multiple video assets require explicit selection")
    asset = MediaAssetStore(dsn).get(video_id, rows[0][0])
    if asset is None or asset.video_id != video_id or asset.kind != "video":
        raise ValueError("video media binding unavailable")
    _canonical_object(asset)
    return asset


def _storage_for(asset) -> PrivateS3MediaStorage:
    import wmill

    try:
        config = json.loads(wmill.get_variable(_STORAGE_CONFIG_PATH))
        if not isinstance(config, dict):
            raise ValueError("storage config must be an object")
        storage = PrivateS3MediaStorage(S3MediaStorageConfig(**{
            key: config[key] for key in _STORAGE_FIELDS
        }))
        if storage.bucket != asset.bucket or config["storage_location"] != asset.storage_location:
            raise ValueError("storage location mismatch")
        return storage
    except Exception:
        raise RuntimeError("video preview storage configuration unavailable") from None


def _https_url(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("invalid preview URL")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("invalid preview URL")
    return value


def main(video_id: str):
    """Return a 300-second HTTPS URL only after end-user authorization and HEAD validation."""
    try:
        video = UUID(video_id)
        # Deliberately precedes DB credentials, database reads, and S3 configuration.
        _authorize()
        asset = _load_single_video_asset(_dsn(), video)
        if asset is None:
            return {"has_media": False}
        stored = _canonical_object(asset)
        storage = _storage_for(asset)
        storage.verify_object(stored)
        playback_url = _https_url(
            storage.presigned_read_url(asset.object_key, expires_in=_PREVIEW_SECONDS)
        )
        return {
            "has_media": True,
            "playback_url": playback_url,
            "expires_in_seconds": _PREVIEW_SECONDS,
        }
    except PermissionError:
        raise PermissionError("authenticated media reviewer required") from None
    except Exception:
        raise RuntimeError("video preview unavailable or changed; refresh page") from None
