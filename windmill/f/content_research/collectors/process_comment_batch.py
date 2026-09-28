# /// script
# requires-python = "==3.14.*"
# dependencies = [
#   "douyin-research-platform @ git+https://github.com/lig9904/douyin-research-platform@8101e7f91df7c1f7fd0d5cef5a5fbf43f4e5dcc2",
#   "psycopg[binary]==3.3.6",
#   "wmill==1.815.0",
# ]
# ///
"""Scheduled comment/L2 batch; callers supply only a persisted discovery ID."""
import json
import math
from datetime import date
from dataclasses import replace
from uuid import UUID

import psycopg
from psycopg.conninfo import make_conninfo

from douyin_research.l0l1 import CommentCollector, CommentEvidenceStore, DailyBudgetGuard, L0L1Store
from douyin_research.l2.comment_pipeline import CommentPipeline, CommentPipelineSettings, _validate_settings
from douyin_research.providers.store import PostgresProviderStore
from douyin_research.providers.tikhub_provider import TikHubProvider
from douyin_research.providers.transport import TikHubTransport

DATABASE_PATH = "f/content_research/research_db"
SETTINGS_PATH = "f/content_research/comment_batch_settings"
IDENTITY_PATH = "f/content_research/automation_worker_identity"
KEY_PATH = "f/content_research/tikhub_api_key"
BUDGET_KEY = "scheduled_comment_batch"
POLICY_PATH = "f/content_research/comment_daily_policy"


def _daily_policy():
    import wmill
    try:
        policy = json.loads(wmill.get_variable(POLICY_PATH))
        if set(policy) != {"max_requests", "max_cost_usd", "max_l3_items"}:
            raise ValueError
        for key in ("max_requests", "max_l3_items"):
            value = policy[key]
            if value is None and key == "max_requests":
                continue
            if type(value) is not int or value < 0:
                raise ValueError
        cost = policy["max_cost_usd"]
        if cost is not None and (type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0):
            raise ValueError
        return policy
    except Exception:
        raise RuntimeError("explicit daily comment policy required") from None


def _prepare_day(conn, settings, policy):
    run_date = settings.quota_date
    # Never reset consumption or overwrite an operator's existing daily limits.
    conn.execute("""insert into daily_budget(budget_date,provider,budget_key,max_requests,max_cost,cost_currency)
        values (%s,'tikhub',%s,%s,%s,'USD') on conflict do nothing""",
        (run_date, BUDGET_KEY, policy["max_requests"], policy["max_cost_usd"]))
    conn.execute("""insert into daily_research_quota(quota_date,platform,quota_key,max_items)
        values (%s,'douyin',%s,%s) on conflict do nothing""",
        (run_date, settings.quota_key, policy["max_l3_items"]))


def _configuration():
    import wmill
    try:
        db = wmill.get_resource(DATABASE_PATH)
        dsn = make_conninfo(host=db["host"], port=db.get("port", 5432), user=db["user"],
            password=db["password"], dbname=db["dbname"], sslmode=db.get("sslmode", "prefer"))
        actor = wmill.get_variable(IDENTITY_PATH)
        if not isinstance(actor, str) or not actor.strip() or len(actor) > 120 or any(ord(c) < 32 for c in actor):
            raise ValueError
        values = json.loads(wmill.get_variable(SETTINGS_PATH))
        allowed = {"top_n", "min_score", "quota_key", "comment_count", "max_pages", "max_items", "sample_reason"}
        if not isinstance(values, dict) or set(values) - allowed:
            raise ValueError
        settings = CommentPipelineSettings(**values)
        _validate_settings(settings)
        return dsn, actor.strip() + "/comments", settings
    except Exception:
        raise RuntimeError("comment batch server configuration unavailable") from None


def _preflight(dsn, source, settings):
    with psycopg.connect(dsn) as conn:
        row = conn.execute("select run_type,status,platform,project_id from pipeline_run where id=%s", (source,)).fetchone()
        if row is None or row[:3] != ("l0l1_discovery", "success", "douyin"):
            raise ValueError("successful Douyin discovery batch required")
        if row[3] is not None:
            # Fail before budget preparation or credential lookup. Project
            # comment/L2 must be introduced with its own subject gate.
            raise ValueError("project discovery batch requires project-specific comment processing")
        novelty_clause = (
            " and coalesce((metadata->>'new_candidate')::boolean,false)"
            if getattr(settings, "new_candidates_only", False)
            else ""
        )
        count = conn.execute(
            "select count(*) from pipeline_run_item where run_id=%s "
            "and entity_type='video' and stage='L1' and outcome='scored'"
            + novelty_clause,
            (source,),
        ).fetchone()[0]
        if not 1 <= count <= 20:
            raise ValueError("discovery batch must contain 1 to 20 scored videos")
        _prepare_day(conn, settings, _daily_policy())
        budget = conn.execute("select cost_currency from daily_budget where budget_date=%s and provider='tikhub' and budget_key=%s",
                              (settings.quota_date, BUDGET_KEY)).fetchone()
        if budget != ("USD",):
            raise RuntimeError("daily comment request accounting must be configured in USD")
        quota = conn.execute("select 1 from daily_research_quota where quota_date=%s and platform='douyin' and quota_key=%s",
                             (settings.quota_date, settings.quota_key)).fetchone()
        if quota is None:
            raise RuntimeError("daily promotion quota must be configured")


def _budget_hook(dsn, run_date):
    guard = DailyBudgetGuard(dsn)
    def reserve(spec):
        guard.acquire(provider="tikhub", budget_key=BUDGET_KEY, requests=1,
                      estimated_cost=spec.unit_cost_usd if spec.paid else 0.0,
                      budget_date=run_date)
    return reserve


class _SafeTransport:
    def __init__(self, transport):
        self.transport = transport

    def call(self, spec, kwargs):
        try:
            return self.transport.call(spec, kwargs)
        except Exception:
            raise RuntimeError("comment provider request failed") from None


def main(source_run_id: str) -> dict:
    source = UUID(source_run_id)
    dsn, actor, settings = _configuration()
    # This entrypoint is schedule-owned.  Manual collection keeps the core
    # default (all scored items); unattended work is restricted to candidates
    # first inserted by the bound discovery run.
    settings = replace(
        settings,
        quota_date=date.today(),
        new_candidates_only=True,
    )
    _preflight(dsn, source, settings)
    import wmill
    transport = None
    try:
        key = wmill.get_variable(KEY_PATH)
        if not isinstance(key, str) or not key.strip():
            raise ValueError
        transport = TikHubTransport(key.strip(), max_retries=0)
        provider = TikHubProvider(transport=_SafeTransport(transport), store=PostgresProviderStore(dsn),
            before_external_call=_budget_hook(dsn, settings.quota_date))
        collector = CommentCollector(provider=provider, evidence_store=CommentEvidenceStore(dsn), run_store=L0L1Store(dsn))
        result = CommentPipeline(dsn, collector=collector, worker_identity=actor).run(source, settings=settings)
        if any(item.outcome != "success" for item in result.videos):
            raise RuntimeError("incomplete batch")
        return {"status": "completed", "run_id": str(result.pipeline_run_id),
                "source_run_id": str(source), "video_count": len(result.videos),
                "selected_count": result.promotion.selected_count if result.promotion else 0}
    except Exception:
        raise RuntimeError("comment batch failed; inspect persisted pipeline evidence") from None
    finally:
        if transport is not None:
            transport.close()
