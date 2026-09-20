# /// script
# requires-python = "==3.13.*"
# dependencies = [
#   "douyin-research-platform @ git+https://github.com/lig9904/douyin-research-platform@3bb9d854f19b62f7fe08a62d6bf6af25c6e86c56",
#   "psycopg[binary]==3.3.6", "wmill==1.815.0",
# ]
# ///
"""Run one persisted L3 approval using operator-owned configuration."""
import json
import math
from dataclasses import replace
from datetime import date
from uuid import UUID

import psycopg
from psycopg.conninfo import make_conninfo
from douyin_research.l3.evidence import L3_REVIEW_IDENTITY_SOURCE
from douyin_research.l3.execution import L3_BUDGET_KEY, L3_CONFIRMATION
from douyin_research.l3.live_service import (
    LiveArkConfiguration, ReviewedL3Selection, execute_reviewed_live_ark,
    live_ark_task_key,
)
from douyin_research.l3.results import L3_SCHEMA_VERSION
from douyin_research.providers.volcengine_ark_l3 import VOLCENGINE_ARK_L3_PROVIDER


def _configuration():
    import wmill
    try:
        db = wmill.get_resource("f/content_research/research_db")
        dsn = make_conninfo(host=db["host"], port=db.get("port", 5432), user=db["user"],
            password=db["password"], dbname=db["dbname"], sslmode=db.get("sslmode", "prefer"))
        cfg = json.loads(wmill.get_variable("f/content_research/l3_worker_config"))
        if set(cfg) != {"ark", "prompt_version", "max_daily_requests", "max_daily_cost_cny"}:
            raise ValueError
        if not isinstance(cfg["prompt_version"], str) or not cfg["prompt_version"].strip():
            raise ValueError
        limit = cfg["max_daily_requests"]
        if limit is not None and (type(limit) is not int or limit < 0):
            raise ValueError
        cost = cfg["max_daily_cost_cny"]
        if cost is not None and (type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0):
            raise ValueError
        ark = LiveArkConfiguration(**cfg["ark"])
        if ark.cost_currency != "CNY":
            raise ValueError
        return dsn, cfg, ark
    except Exception:
        raise RuntimeError("L3 worker configuration unavailable") from None


def _selection(dsn, approval_id, prompt_version):
    with psycopg.connect(dsn) as conn:
        conn.execute("set transaction read only")
        row = conn.execute("""select a.video_id, a.value from human_annotation a
            where a.id=%s and a.annotation_type='l3_privacy_review'
            and a.value->>'reviewer_identity_source'=%s
            and not exists (select 1 from human_annotation newer
                where newer.video_id=a.video_id and newer.annotation_type='l3_privacy_review'
                and (newer.created_at,newer.id) > (a.created_at,a.id))""",
            (approval_id, L3_REVIEW_IDENTITY_SOURCE)).fetchone()
    if row is None or row[1].get("reviewed") is not True:
        raise ValueError("Approved evidence unavailable")
    video_id, value = row
    return ReviewedL3Selection(video_id=video_id,
        privacy_review_version=value["version"],
        expected_input_fingerprint=value["evidence_fingerprint"],
        expected_evidence_version=value["evidence_version"],
        prompt_version=prompt_version, estimated_llm_cost=None,
        confirmation=L3_CONFIRMATION)


def _reservation_date(dsn, selection, ark, cfg):
    task_key = live_ark_task_key(selection.video_id, selection.expected_input_fingerprint,
        model_id=ark.model_id, model_revision=ark.model_revision,
        prompt_version=selection.prompt_version, schema_version=L3_SCHEMA_VERSION)
    with psycopg.connect(dsn) as conn:
        prior = conn.execute("select budget_date from l3_execution_job where task_key=%s",
            (task_key,)).fetchone()
        run_date = prior[0] if prior else date.today()
        if prior:
            budget = conn.execute("""select 1 from daily_budget
                where budget_date=%s and provider=%s and budget_key=%s""",
                (run_date, VOLCENGINE_ARK_L3_PROVIDER, L3_BUDGET_KEY)).fetchone()
            if budget is None:
                raise RuntimeError("Existing L3 accounting requires reconciliation")
        else:
            conn.execute("""insert into daily_budget(budget_date,provider,budget_key,max_requests,max_cost,cost_currency)
                values (%s,%s,%s,%s,%s,'CNY') on conflict do nothing""",
                (run_date, VOLCENGINE_ARK_L3_PROVIDER, L3_BUDGET_KEY,
                 cfg["max_daily_requests"], cfg["max_daily_cost_cny"]))
    return run_date


def main(approval_id: str) -> dict:
    approval = UUID(approval_id)
    try:
        dsn, cfg, ark = _configuration()
        selection = _selection(dsn, approval, cfg["prompt_version"])
        run_date = _reservation_date(dsn, selection, ark, cfg)
        result = execute_reviewed_live_ark(dsn,
            selection=replace(selection, budget_date=run_date), ark=ark)
        if result.get("status") != "completed":
            raise RuntimeError("L3 requires operator attention")
        return result
    except Exception:
        raise RuntimeError("L3 task failed; inspect persisted execution state and evidence review") from None
