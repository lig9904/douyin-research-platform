# /// script
# requires-python = "==3.14.*"
# dependencies = ["douyin-research-platform @ git+https://github.com/lig9904/douyin-research-platform@e0120bb3742bf636375f22fb0139d093e4ab2de7", "psycopg[binary]==3.3.6", "wmill==1.815.0"]
# ///
"""Internal-only project L3 worker. No Raw App path calls this runnable."""
from __future__ import annotations

import json
from uuid import UUID
import psycopg
from psycopg.conninfo import make_conninfo

from douyin_research.l3.live_service import LiveArkConfiguration, validate_live_ark_configuration
from douyin_research.project_analysis.l3 import ProjectL3ExecutionSelection, ProjectL3ExecutionService
from douyin_research.providers.volcengine_ark_l3 import VerifiedLiveVolcengineArkL3Provider


def _configuration():
    import wmill
    try:
        db = wmill.get_resource("f/content_research/research_db")
        dsn = make_conninfo(host=db["host"], port=db.get("port", 5432), user=db["user"],
                            password=db["password"], dbname=db["dbname"], sslmode=db.get("sslmode", "prefer"))
        cfg = json.loads(wmill.get_variable("f/content_research/l3_worker_config"))
        if set(cfg) != {"ark", "prompt_version", "max_daily_requests", "max_daily_cost_cny"}:
            raise ValueError
        ark = LiveArkConfiguration(**cfg["ark"])
        validate_live_ark_configuration(ark)
        if not isinstance(cfg["prompt_version"], str) or not cfg["prompt_version"].strip():
            raise ValueError
        return dsn, ark, cfg["prompt_version"]
    except Exception:
        raise RuntimeError("PROJECT_L3_WORKER_CONFIGURATION_UNAVAILABLE") from None


def _selection(dsn: str, review_id: str, prompt_version: str, ark: LiveArkConfiguration) -> ProjectL3ExecutionSelection:
    review = UUID(review_id)
    with psycopg.connect(dsn) as conn:
        row = conn.execute("""select project_id,video_id,transcript_id,review_version,evidence_fingerprint
                              from project_l3_privacy_review where id=%s and status='approved'""", (review,)).fetchone()
    if row is None:
        raise ValueError("project review unavailable")
    return ProjectL3ExecutionSelection(project_id=row[0], video_id=row[1], review_id=review,
        transcript_id=row[2], review_version=row[3], evidence_fingerprint=row[4], model_id=ark.model_id,
        model_revision=ark.model_revision, prompt_version=prompt_version, cost_currency=ark.cost_currency)


def main(review_id: str) -> dict:
    dsn, ark, prompt_version = _configuration()
    try:
        selection = _selection(dsn, review_id, prompt_version, ark)
        def provider_factory():
            return VerifiedLiveVolcengineArkL3Provider(
                api_key=ark.api_key, endpoint_id=ark.endpoint_id, model_id=ark.model_id,
                model_revision=ark.model_revision, expected_response_model=ark.expected_response_model,
                cost_currency=ark.cost_currency, pricing_version=ark.pricing_version,
                input_cost_per_million_tokens=ark.input_cost_per_million_tokens,
                output_cost_per_million_tokens=ark.output_cost_per_million_tokens,
                timeout_seconds=ark.timeout_seconds)
        return ProjectL3ExecutionService(dsn).run(selection, provider_factory=provider_factory)
    except Exception:
        raise RuntimeError("PROJECT_L3_EXECUTION_REQUIRES_ATTENTION") from None
