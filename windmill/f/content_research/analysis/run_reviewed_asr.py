# /// script
# requires-python = "==3.13.*"
# dependencies = [
#   "douyin-research-platform @ git+https://github.com/lig9904/douyin-research-platform@8849f6e742f800b35c8f7ec431f488b7638b361e",
#   "psycopg[binary]==3.3.6", "wmill==1.815.0",
# ]
# ///
"""Trusted scheduled ASR entrypoint. No caller URLs, credentials or actor."""
import json
import math
from datetime import date
from dataclasses import replace
from uuid import UUID

import psycopg
from psycopg.conninfo import make_conninfo
from douyin_research.l2.asr_execution import ASR_BUDGET_KEY
from douyin_research.l2.live_asr_service import LiveASRService, request_from_reviewed_asset, live_asr_task_key
from douyin_research.media_storage import PrivateS3MediaStorage, S3MediaStorageConfig
from douyin_research.providers.volcengine_asr import VOLCENGINE_ASR_PROVIDER


def _configuration():
    import wmill
    try:
        db = wmill.get_resource("f/content_research/research_db")
        dsn = make_conninfo(host=db["host"], port=db.get("port", 5432), user=db["user"],
            password=db["password"], dbname=db["dbname"], sslmode=db.get("sslmode", "prefer"))
        cfg = json.loads(wmill.get_variable("f/content_research/asr_worker_config"))
        if set(cfg) != {"api_key", "max_polls", "max_daily_requests", "max_daily_cost_cny"}:
            raise ValueError
        if not isinstance(cfg["api_key"], str) or not cfg["api_key"].strip():
            raise ValueError
        if type(cfg["max_polls"]) is not int or not 0 <= cfg["max_polls"] <= 3:
            raise ValueError
        limit = cfg["max_daily_requests"]
        if limit is not None and (type(limit) is not int or limit < 0):
            raise ValueError
        cost = cfg["max_daily_cost_cny"]
        if cost is not None and (type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0):
            raise ValueError
        media = json.loads(wmill.get_variable("f/content_research/media_storage_config"))
        storage = PrivateS3MediaStorage(S3MediaStorageConfig(**{k: media[k] for k in (
            "endpoint", "public_endpoint", "bucket", "region", "access_key_id",
            "secret_access_key", "force_path_style", "use_ssl")}))
        return dsn, cfg, media, storage
    except Exception:
        raise RuntimeError("ASR worker configuration unavailable") from None


def main(video_id: str, asset_id: str, review_version: str, resume_job_id: str | None = None) -> dict:
    video, asset = UUID(video_id), UUID(asset_id)
    resume_id = UUID(resume_job_id) if resume_job_id is not None else None
    try:
        dsn, cfg, media, storage = _configuration()
        request = request_from_reviewed_asset(dsn, video_id=video, asset_id=asset,
            review_version=review_version, storage=storage, public_origin=media["public_endpoint"],
            storage_location=media["storage_location"], api_key=cfg["api_key"], max_polls=cfg["max_polls"])
        with psycopg.connect(dsn) as conn:
            task_key = live_asr_task_key(video, request.source_fingerprint)
            if resume_id is not None:
                saved = conn.execute("""select task_key,status from asr_execution_job
                    where id=%s and provider=%s and provider_task_ref is not null
                    and submission_count=1 and budget_key=%s""",
                    (resume_id, VOLCENGINE_ASR_PROVIDER, ASR_BUDGET_KEY)).fetchone()
                if saved is None or saved[0] != task_key or saved[1] not in {"submitted", "running", "completed"}:
                    raise RuntimeError("ASR resume identity or state changed")
                if cfg["max_polls"] < 1:
                    raise RuntimeError("ASR polling disabled in worker configuration")
            prior = conn.execute("select budget_date from asr_execution_job where task_key=%s",
                (task_key,)).fetchone()
            if resume_id is not None and prior is None:
                raise RuntimeError("ASR resume reservation missing")
            run_date = prior[0] if prior else date.today()
            if prior:
                budget = conn.execute("""select 1 from daily_budget
                    where budget_date=%s and provider=%s and budget_key=%s""",
                    (run_date, VOLCENGINE_ASR_PROVIDER, ASR_BUDGET_KEY)).fetchone()
                if budget is None:
                    raise RuntimeError("Existing ASR accounting requires reconciliation")
            else:
                conn.execute("""insert into daily_budget(budget_date,provider,budget_key,max_requests,max_cost,cost_currency)
                    values (%s,%s,%s,%s,%s,'CNY') on conflict do nothing""",
                    (run_date, VOLCENGINE_ASR_PROVIDER, ASR_BUDGET_KEY,
                     cfg["max_daily_requests"], cfg["max_daily_cost_cny"]))
        result = LiveASRService(dsn).run(replace(request, budget_date=run_date))
        if result.get("error_code") or result.get("status") not in {"submitted", "running", "completed"}:
            raise RuntimeError("ASR requires operator attention")
        return result
    except Exception:
        raise RuntimeError("ASR task failed; inspect persisted execution state") from None
