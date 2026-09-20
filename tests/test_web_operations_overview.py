from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

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
        assert "request_fingerprint" not in call and "metadata" not in call
        assert any(row["known_total"] == pytest.approx(6) for row in result["task_costs"])
        task = next(row for row in result["tasks"] if row["task_type"] == "ops-task")
        assert task["total_cost"] == pytest.approx(6)
        assert "input_fingerprint" not in task and "metadata" not in task
        assert "request_fingerprint" in result["excluded_fields"]
    finally:
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute("delete from external_api_call where endpoint_key='ops-fixture'")
            cur.execute("delete from source_video where id=%s", (video_id,))
            cur.execute("delete from pipeline_run where id=%s", (run_id,))
            conn.commit()
