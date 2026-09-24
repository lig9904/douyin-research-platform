# /// script
# requires-python = "==3.14.*"
# dependencies = ["douyin-research-platform @ git+https://github.com/lig9904/douyin-research-platform@e0120bb3742bf636375f22fb0139d093e4ab2de7", "psycopg[binary]==3.3.6", "wmill==1.815.0"]
# ///
"""Poll one already-submitted project ASR task; it can never submit media."""
from __future__ import annotations

import json
from uuid import UUID

import psycopg
from psycopg.conninfo import make_conninfo

from douyin_research.project_analysis.asr_backend import ProjectASRService
from douyin_research.providers.volcengine_asr import ReviewedASRMediaDelivery, VerifiedLiveVolcengineDoubaoASRProvider

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
        return dsn, worker, media
    except Exception:
        raise RuntimeError("project ASR poller configuration unavailable") from None


def main(project_id: str, task_key: str) -> dict:
    project = UUID(project_id)
    if not isinstance(task_key, str) or not task_key.startswith("project-asr-v1:") or len(task_key) != 79:
        raise ValueError("project ASR task key is invalid")
    dsn, worker, media = _configuration()
    with psycopg.connect(dsn) as conn:
        row = conn.execute("""select job.review_version,job.source_fingerprint,asset.bucket,asset.object_key
                            from project_asr_execution_job job join media_asset asset on asset.id=job.reviewed_asset_id
                            where job.project_id=%s and job.task_key=%s and job.status in ('submitted','running')
                              and job.provider_task_ref is not null""", (project, task_key)).fetchone()
    if row is None:
        return {"status": "reconciliation_required", "external_calls": 0, "created": False}
    review_version, fingerprint, bucket, object_key = row
    # Poll does not deliver media. The provider still demands a reviewed type;
    # this canonical no-query URL is never sent to the polling request.
    delivery = ReviewedASRMediaDelivery(
        url=f"{media['public_endpoint'].rstrip('/')}/{bucket}/{object_key}", review_version=review_version,
    )

    def provider():
        return VerifiedLiveVolcengineDoubaoASRProvider(
            reviewed_media_delivery=delivery, api_key=worker["api_key"], audio_format="wav",
            source_fingerprint=fingerprint, source_provider="project-private-object-storage",
            cost_currency="CNY", timeout_seconds=30,
        )

    service = ProjectASRService(dsn, delivery_origin=media["public_endpoint"],
                                trusted_worker_actor=worker["service_reviewer_email"])
    result = service.resume_poll(project_id=project, task_key=task_key, provider_factory=provider)
    return {**result, "cost_basis": "unknown", "worker_identity": "server_configured"}
