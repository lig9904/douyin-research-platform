# /// script
# requires-python = "==3.13.*"
# dependencies = [
#   "douyin-research-platform @ git+https://github.com/lig9904/douyin-research-platform@fd2694729c784a0f2e34b2c0a4d6746da57813fe",
#   "psycopg[binary]==3.3.6",
#   "wmill==1.815.0",
# ]
# ///

"""Unattended media worker: only a persisted video ID is caller-controlled.

No paid API requests, privacy approvals or public bucket changes are made.
Credentials, CDN hosts, storage and runtime paths come from server configuration.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TypedDict
from uuid import UUID

import psycopg
from psycopg.conninfo import make_conninfo

from douyin_research.media_ingestion import MediaIngestionConfiguration, MediaIngestionService
from douyin_research.media_storage import PrivateS3MediaStorage, S3MediaStorageConfig


CONFIG_PATH = "f/content_research/media_storage_config"
IDENTITY_PATH = "f/content_research/automation_worker_identity"
DATABASE_PATH = "f/content_research/research_db"


class postgresql(TypedDict):
    host: str
    port: int
    user: str
    password: str
    dbname: str
    sslmode: str


def _variable(path: str) -> str:
    import wmill
    return wmill.get_variable(path)


def _database_resource() -> postgresql:
    import wmill
    return wmill.get_resource(DATABASE_PATH)


def _preflight(dsn: str, video_id: UUID) -> None:
    with psycopg.connect(dsn) as conn:
        if conn.execute("select to_regclass('media_asset')").fetchone()[0] is None:
            raise RuntimeError("media schema is not deployed")
        row = conn.execute("select platform from source_video where id=%s", (video_id,)).fetchone()
        if row is None or row[0] != "douyin":
            raise ValueError("an existing Douyin video is required")


def _configuration() -> tuple[S3MediaStorageConfig, MediaIngestionConfiguration]:
    try:
        data = json.loads(_variable(CONFIG_PATH))
        identity = _variable(IDENTITY_PATH).strip()
        if not isinstance(data, dict) or not identity or len(identity) > 160:
            raise ValueError
        required = {
            "endpoint", "public_endpoint", "bucket", "region", "access_key_id", "secret_access_key",
            "force_path_style", "use_ssl", "storage_location", "temp_directory",
            "allowed_download_hosts", "ffmpeg_binary",
        }
        if not required <= data.keys() or data.keys() - required - {"max_download_bytes"}:
            raise ValueError
        hosts = data["allowed_download_hosts"]
        if not isinstance(hosts, list) or not hosts or not all(isinstance(h, str) and h.strip() for h in hosts):
            raise ValueError
        maximum = data.get("max_download_bytes", 2 * 1024 ** 3)
        if type(maximum) is not int or maximum <= 0:
            raise ValueError
        temp = Path(data["temp_directory"])
        if not temp.is_absolute() or not temp.is_dir():
            raise ValueError
        executable = shutil.which(data["ffmpeg_binary"])
        if executable is None:
            raise ValueError
        storage = S3MediaStorageConfig(**{k: data[k] for k in (
            "endpoint", "public_endpoint", "bucket", "region", "access_key_id",
            "secret_access_key", "force_path_style", "use_ssl",
        )})
        worker = MediaIngestionConfiguration(
            storage_location=data["storage_location"], bucket=storage.bucket,
            temp_directory=temp, allowed_download_hosts=tuple(hosts),
            worker_identity=f"{identity}/media", ffmpeg_binary=executable,
            max_download_bytes=maximum,
        )
        return storage, worker
    except Exception:
        # Even JSON parsing or configuration-provider failures must not expose
        # the raw secret blob, credentials or a temporary signed address.
        raise RuntimeError("media worker configuration or runtime is not ready") from None


def main(video_id: str) -> dict:
    identifier = UUID(video_id)
    try:
        db = _database_resource()
        dsn = make_conninfo(host=db["host"], port=int(db.get("port", 5432)),
            user=db["user"], password=db["password"], dbname=db["dbname"],
            sslmode=db.get("sslmode", "prefer"))
    except Exception:
        raise RuntimeError("research database configuration unavailable") from None
    _preflight(dsn, identifier)
    storage_config, worker_config = _configuration()
    try:
        result = MediaIngestionService(dsn, PrivateS3MediaStorage(storage_config), worker_config).run(identifier)
    except Exception:
        raise RuntimeError("media worker execution failed") from None
    if result.get("status") == "failed":
        # A normal function return would incorrectly mark the Windmill job
        # successful. Detailed safe stages are already in pipeline_run.
        raise RuntimeError("media ingestion failed; inspect persisted pipeline run")
    return result
