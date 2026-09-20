# /// script
# requires-python = "==3.13.*"
# dependencies = [
#   "douyin-research-platform @ git+https://github.com/lig9904/douyin-research-platform@92153f603368a3ab2cb7810924d6d3948857987a",
#   "psycopg[binary]==3.3.6", "wmill==1.815.0",
# ]
# ///

"""Authenticated review only; does not submit ASR or create public media URLs."""
from uuid import UUID
import os
import json
import psycopg

from psycopg.conninfo import make_conninfo
from douyin_research.l2.media_review import prepare_asr_media, approve_asr_media
from douyin_research.media_assets import MediaAssetStore
from douyin_research.media_storage import PrivateS3MediaStorage, S3MediaStorageConfig, StoredMediaObject


def _dsn():
    import wmill
    try:
        db = wmill.get_resource("f/content_research/research_db")
        return make_conninfo(host=db["host"], port=db.get("port", 5432), user=db["user"],
            password=db["password"], dbname=db["dbname"], sslmode=db.get("sslmode", "prefer"))
    except Exception:
        raise RuntimeError("media review database configuration unavailable") from None


def _list_assets(dsn, video, review_version):
    import wmill
    from douyin_research.l3.review import authorize_reviewer
    authorize_reviewer(os.environ.get("WM_END_USER_EMAIL"),
                       wmill.get_variable("f/content_research/l3_privacy_reviewers"))
    with psycopg.connect(dsn) as conn:
        rows = conn.execute("select id from media_asset where video_id=%s and kind='audio' order by id limit 21", (video,)).fetchall()
    if len(rows) > 20:
        raise ValueError("too many audio assets; explicit selection required")
    return {"video_id": str(video), "assets": [prepare_asr_media(dsn, video_id=video,
        asset_id=row[0], review_version=review_version) for row in rows],
        "external_calls": 0, "db_writes": 0}


def _playback(dsn, video, asset, review_version):
    # Authorization and video/asset binding precede loading storage credentials.
    manifest = prepare_asr_media(dsn, video_id=video, asset_id=asset, review_version=review_version)
    reference = MediaAssetStore(dsn).get(video, asset)
    if reference is None:
        raise ValueError("media unavailable")
    import wmill
    config = json.loads(wmill.get_variable("f/content_research/media_storage_config"))
    storage = PrivateS3MediaStorage(S3MediaStorageConfig(**{key: config[key] for key in (
        "endpoint", "public_endpoint", "bucket", "region", "access_key_id",
        "secret_access_key", "force_path_style", "use_ssl")}))
    if storage.bucket != reference.bucket or config["storage_location"] != reference.storage_location:
        raise ValueError("storage location mismatch")
    storage.verify_object(StoredMediaObject(key=reference.object_key, sha256=reference.content_sha256,
        size=reference.size_bytes, content_type=reference.content_type))
    return {**manifest, "playback_url": storage.presigned_read_url(reference.object_key, expires_in=300),
            "expires_in_seconds": 300, "external_calls": 1}


def main(video_id: str, asset_id: str = "", review_version: str = "media-v1", action: str = "preview",
         expected_asset_fingerprint: str | None = None):
    if action not in {"list", "preview", "playback", "approve"}:
        raise ValueError("unsupported media review action")
    video = UUID(video_id)
    asset = UUID(asset_id) if action != "list" else None
    if action == "approve" and not expected_asset_fingerprint:
        raise ValueError("current media preview fingerprint is required")
    try:
        dsn = _dsn()
        if action == "list":
            return _list_assets(dsn, video, review_version)
        if action == "preview":
            return prepare_asr_media(dsn, video_id=video, asset_id=asset, review_version=review_version)
        if action == "playback":
            return _playback(dsn, video, asset, review_version)
        return approve_asr_media(dsn, video_id=video, asset_id=asset, review_version=review_version,
                                 expected_asset_fingerprint=expected_asset_fingerprint)
    except PermissionError:
        raise PermissionError("authenticated media reviewer required") from None
    except Exception:
        raise RuntimeError("media review unavailable or changed; refresh preview") from None
