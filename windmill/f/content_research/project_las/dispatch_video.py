# /// script
# requires-python = "==3.14.*"
# dependencies = ["douyin-research-platform @ git+https://github.com/lig9904/douyin-research-platform@5b93ffa7681038ecbd355c5ffbbbb3b5cdb53180", "psycopg[binary]==3.3.6", "wmill==1.815.0"]
# ///
"""Trusted exactly-once LAS Submit for one persisted project attempt."""
from __future__ import annotations

import os
import re
from uuid import UUID

from psycopg.conninfo import make_conninfo

from douyin_research.media_storage import PrivateS3MediaStorage, S3MediaStorageConfig
from douyin_research.project_analysis.las import ProjectLASService
from douyin_research.providers.volcengine_las import VolcengineLASProvider


_SCOPE = re.compile(r"[A-Za-z0-9._:-]{1,160}\Z")
_MEDIA_FIELDS = (
    "endpoint", "public_endpoint", "bucket", "region", "access_key_id",
    "secret_access_key", "force_path_style", "use_ssl",
)


def _configuration():
    import wmill

    try:
        worker_email = wmill.get_variable("f/content_research/project_las_worker_email")
        if not isinstance(worker_email, str) or "@" not in worker_email:
            raise ValueError
        actual_actor = (os.environ.get("WM_END_USER_EMAIL") or os.environ.get("WM_EMAIL") or "").strip().lower()
        if actual_actor != worker_email.strip().lower():
            raise PermissionError("project LAS worker identity mismatch")
        db = wmill.get_resource("f/content_research/research_db")
        dsn = make_conninfo(
            host=db["host"], port=int(db.get("port", 5432)), user=db["user"],
            password=db["password"], dbname=db["dbname"],
            sslmode=db.get("sslmode", "prefer"),
        )
        import json
        api_key = wmill.get_variable("f/content_research/las_video_understanding_api_key")
        media = json.loads(wmill.get_variable("f/content_research/media_storage_config"))
        scope = wmill.get_variable("f/content_research/project_las_account_scope")
        if not isinstance(api_key, str) or not api_key:
            raise ValueError
        if not isinstance(scope, str) or not _SCOPE.fullmatch(scope):
            raise ValueError
        storage = PrivateS3MediaStorage(S3MediaStorageConfig(**{
            key: media[key] for key in _MEDIA_FIELDS
        }))
        return dsn, api_key, worker_email, media, scope, storage
    except Exception:
        raise RuntimeError("project LAS worker configuration unavailable") from None


def main(attempt_id: str) -> dict:
    attempt = UUID(attempt_id)
    dsn, api_key, worker_email, media, scope, storage = _configuration()
    service = ProjectLASService(
        dsn, delivery_origin=media["public_endpoint"],
        trusted_worker_actor=worker_email,
        trusted_storage_location=media["storage_location"],
        trusted_bucket=media["bucket"], account_scope=scope,
    )
    with VolcengineLASProvider(api_key=api_key, timeout_seconds=30) as provider:
        return service.dispatch(attempt_id=attempt, storage=storage, provider=provider)
