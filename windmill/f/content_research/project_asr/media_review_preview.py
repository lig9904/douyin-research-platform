# /// script
# requires-python = "==3.14.*"
# dependencies = ["douyin-research-platform", "psycopg[binary]==3.3.6", "wmill==1.815.0"]
# ///
"""Authenticated project-private ASR audio review preview; no write/ASR call."""
import json
from uuid import UUID
from psycopg.conninfo import make_conninfo
from douyin_research.media_assets import MediaAssetStore
from douyin_research.media_storage import PrivateS3MediaStorage, S3MediaStorageConfig
from douyin_research.project_analysis.asr_backend import ProjectASRService, ProjectMediaReviewInput, asset_manifest

def _config():
    import wmill
    try:
        db = wmill.get_resource("f/content_research/research_db")
        dsn = make_conninfo(host=db["host"], port=db.get("port", 5432), user=db["user"], password=db["password"], dbname=db["dbname"], sslmode=db.get("sslmode", "prefer"))
        raw = json.loads(wmill.get_variable("f/content_research/media_storage_config"))
        storage = PrivateS3MediaStorage(S3MediaStorageConfig(**{key: raw[key] for key in ("endpoint","public_endpoint","bucket","region","access_key_id","secret_access_key","force_path_style","use_ssl")}))
        return dsn, raw["public_endpoint"], storage
    except Exception: raise RuntimeError("project ASR review configuration unavailable") from None

def main(project_id: str, video_id: str, asset_id: str, review_version: str) -> dict:
    dsn, origin, storage = _config()
    request = ProjectMediaReviewInput(UUID(project_id), UUID(video_id), UUID(asset_id), review_version, {"preview": True})
    result = ProjectASRService(dsn, delivery_origin=origin).preview(request)
    asset = MediaAssetStore(dsn).get(UUID(video_id), UUID(asset_id))
    if asset is None: raise RuntimeError("approved project audio asset unavailable")
    if asset_manifest(asset, origin) != result["asset_manifest_fingerprint"]:
        raise RuntimeError("project audio changed since review preview")
    # Returned only to the authenticated reviewer; never stored in DB/audit output.
    result["audio_preview_url"] = storage.presigned_read_url(asset.object_key, expires_in=300)
    result["audio_preview_expires_seconds"] = 300
    return result
