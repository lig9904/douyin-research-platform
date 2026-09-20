from __future__ import annotations

import importlib.util
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest


DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not configured")


def _backend():
    path = Path("windmill/f/content_research/research_dashboard.raw_app/backend/get_operations_overview.py")
    spec = importlib.util.spec_from_file_location("get_operations_overview", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _resource() -> dict[str, object]:
    assert DSN
    parsed = urlparse(DSN)
    return {
        "host": parsed.hostname or "127.0.0.1", "port": parsed.port or 5432,
        "user": parsed.username or "", "password": parsed.password or "",
        "dbname": parsed.path.lstrip("/"), "sslmode": "disable",
    }


def test_operations_is_readonly_bounded_and_hides_sensitive_fields() -> None:
    assert DSN
    video_id, run_id = uuid4(), uuid4()
    suffix = str(uuid4())
    task_key = f"web-ops-{suffix}"
    try:
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute("insert into source_video(id, platform, platform_video_id, title) values (%s, 'douyin', %s, 'ops fixture')", (video_id, suffix))
            cur.execute("""insert into pipeline_run(id, run_type, run_version, platform, status, input_count, output_count, summary, error_summary)
                           values (%s, 'ops-fixture', 'v1', 'douyin', 'failed', 1, 0, '{"safe":true}'::jsonb, '{"secret":"never return"}'::jsonb)""", (run_id,))
            cur.execute("""insert into research_task_cost(
              task_key, task_type, task_version, pipeline_run_id, video_id, status,
              input_fingerprint, output_fingerprint, api_cost, asr_cost, llm_cost,
              cost_currency, cost_basis, metadata
            ) values (%s, 'ops-task', 'v1', %s, %s, 'completed',
              'private-input', 'private-output', 1, 2, 3, 'CNY', 'actual', '{"secret":"never return"}'::jsonb)""", (task_key, run_id, video_id))
            cur.execute("""insert into external_api_call(
              provider, platform, endpoint_key, request_fingerprint, status, cached,
              actual_cost, cost_currency, metadata
            ) values ('private-provider', 'douyin', 'ops-fixture', 'private-request', 'failed', false,
              0.5, 'USD', '{"raw":"never return"}'::jsonb)""")
            cur.execute("""insert into external_api_call(
              provider, platform, endpoint_key, request_fingerprint, status, cached,
              estimated_cost, actual_cost, cost_currency, metadata
            ) values ('tikhub', 'douyin', 'ops-estimated', 'estimated-request', 'success', false,
              0.001, null, 'USD', '{"cost_basis":"estimated_unit_price","price_source":"official-tariff","pricing_version":"2026-09-20","http_attempt_count":1,"billing_status":"estimated"}'::jsonb)""")
            cur.execute("""insert into external_api_call(
              provider, platform, endpoint_key, request_fingerprint, status, cached,
              estimated_cost, actual_cost, cost_currency, metadata
            ) values ('tikhub', 'douyin', 'ops-estimated-unknown', 'unknown-attempt-request', 'success', false,
              0.001, null, 'USD', '{"cost_basis":"estimated_unit_price","http_attempt_count":3,"unknown_attempt_count":1,"billing_status":"unknown"}'::jsonb)""")
            cur.execute("""insert into external_api_call(
              provider, platform, endpoint_key, request_fingerprint, status, cached,
              estimated_cost, actual_cost, cost_currency, metadata
            ) values ('tikhub', 'douyin', 'ops-unpriced-estimate', 'missing-price-request', 'success', false,
              null, null, 'USD', '{"cost_basis":"estimated_unit_price","billing_status":"estimated"}'::jsonb)""")
            cur.execute("""insert into external_api_call(
              provider, platform, endpoint_key, request_fingerprint, status, cached,
              estimated_cost, actual_cost, cost_currency, metadata
            ) values ('tikhub', 'douyin', 'ops-legacy-quote', 'legacy-request', 'success', false,
              null, 0.001, 'USD', '{"cost_basis":"verified_unit_price"}'::jsonb)""")
            cur.execute("""insert into external_api_call(
              provider, platform, endpoint_key, request_fingerprint, status, cached,
              actual_cost, cost_currency, metadata
            ) values ('tikhub', 'douyin', 'ops-billed', 'billed-request', 'success', false,
              0.002, 'USD', '{"cost_basis":"supplier_bill"}'::jsonb)""")
            cur.execute("""insert into external_api_call(
              provider, platform, endpoint_key, request_fingerprint, status, cached,
              actual_cost, cost_currency, metadata
            ) values ('tikhub', 'douyin', 'ops-free', 'free-request', 'success', true,
              0, 'USD', '{"cost_basis":"cache_zero","billing_status":"known_zero"}'::jsonb)""")
            conn.commit()

        result = _backend().main(_resource(), platform="douyin", days=999, page=1, page_size=1)
        assert result["readonly"] is True
        assert result["days"] == 90
        assert result["page_size"] == 10
        assert result["api_summary"]["failed_calls"] >= 1
        call = next(row for row in result["api_calls"] if row["endpoint_key"] == "ops-fixture")
        assert call["actual_cost"] == pytest.approx(0.5)
        assert call["cost_currency"] == "USD"
        assert call["cost_basis"] == "unpriced"
        assert call["cost_status"] == "unknown"
        assert call["billing_status"] == "unknown"
        assert call["http_attempt_count"] is None
        assert "request_fingerprint" not in call and "metadata" not in call
        estimated = next(row for row in result["api_calls"] if row["endpoint_key"] == "ops-estimated")
        assert estimated["cost_status"] == "estimated"
        assert estimated["billing_status"] == "estimated"
        assert estimated["http_attempt_count"] == 1
        uncertain = next(row for row in result["api_calls"] if row["endpoint_key"] == "ops-estimated-unknown")
        assert uncertain["cost_status"] == "estimated"
        assert uncertain["billing_status"] == "unknown"
        assert uncertain["unknown_attempt_count"] == 1
        missing_price = next(row for row in result["api_calls"] if row["endpoint_key"] == "ops-unpriced-estimate")
        assert missing_price["cost_status"] == "unknown"
        assert missing_price["billing_status"] == "unknown"
        billed = next(row for row in result["api_calls"] if row["endpoint_key"] == "ops-billed")
        assert billed["cost_status"] == "reconciled"
        free = next(row for row in result["api_calls"] if row["endpoint_key"] == "ops-free")
        assert free["cost_status"] == "known_zero"
        usd = next(row for row in result["api_costs"] if row["currency"] == "USD")
        assert float(usd["estimated_cost"]) >= 0.003
        assert float(usd["reconciled_cost"]) >= 0.002
        assert usd["known_zero_calls"] >= 1
        assert usd["unknown_cost_calls"] >= 3
        assert any(row["known_total"] == pytest.approx(6) for row in result["task_costs"])
        task = next(row for row in result["tasks"] if row["task_type"] == "ops-task")
        assert task["total_cost"] == pytest.approx(6)
        assert "input_fingerprint" not in task and "metadata" not in task
        assert "request_fingerprint" in result["excluded_fields"]
    finally:
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute("delete from external_api_call where endpoint_key like 'ops-%'")
            cur.execute("delete from source_video where id=%s", (video_id,))
            cur.execute("delete from pipeline_run where id=%s", (run_id,))
            conn.commit()


def test_operations_returns_account_daily_spend_without_platform_filtering() -> None:
    """A US supplier day remains a US supplier day, not a Beijing dashboard day."""
    assert DSN
    scope = f"ops-daily-{uuid4()}"
    billing_date = datetime.now(timezone.utc).astimezone(ZoneInfo("America/Los_Angeles")).date()
    try:
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into supplier_daily_spend(
                  provider, account_scope, billing_date, cost_currency, billing_timezone,
                  total_cost, balance_cost, free_credit_cost, total_requests, paid_requests, fetched_at
                ) values ('tikhub', %s, %s, 'USD', 'America/Los_Angeles',
                  0.052, 0.052, 0, 3, 2, now())
                """,
                (scope, billing_date),
            )
            conn.commit()

        result = _backend().main(_resource(), platform="kuaishou", days=1)
        spend = result["supplier_daily_spend"]
        row = next(item for item in spend["records"] if item["account_scope"] == scope)
        assert spend["status"] == "available"
        assert row["billing_date"] == billing_date.isoformat()
        assert row["billing_timezone"] == "America/Los_Angeles"
        assert row["period_status"] == "current_accumulating"
        assert row["freshness_status"] == "fresh"
        assert row["total_cost"] == pytest.approx(0.052)
        assert row["total_requests"] == 3 and row["paid_requests"] == 2
    finally:
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute("delete from supplier_daily_spend where account_scope=%s", (scope,))
            conn.commit()


def test_operations_daily_spend_does_not_turn_missing_sync_data_into_zero() -> None:
    class Cursor:
        def __init__(self, table_exists: bool):
            self.table_exists = table_exists
            self.calls = 0

        def execute(self, *_args):
            self.calls += 1

        def fetchone(self):
            return {"table_name": "supplier_daily_spend" if self.table_exists else None}

        def fetchall(self):
            return []

    backend = _backend()
    absent = backend._daily_supplier_spend(Cursor(table_exists=False), 7)
    empty = backend._daily_supplier_spend(Cursor(table_exists=True), 7)
    assert absent == {
        "status": "not_synced", "records": [], "today": [],
        "message": "供应商日费用尚未同步。",
    }
    assert empty["status"] == "not_synced"
    assert empty["records"] == [] and empty["today"] == []
    assert "0" not in empty["message"]
