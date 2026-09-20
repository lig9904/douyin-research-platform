"""Opt-in real TikHub / PostgreSQL golden-path integration test.

It is intentionally excluded from normal CI and only makes up to two uncached
external calls when every gate is supplied by the operator.
"""

from __future__ import annotations

import os

import psycopg
import pytest

from douyin_research.l0l1.real_data import GOLDEN_BUDGET_KEY, make_plan, run_live


DSN = os.getenv("DATABASE_URL")
API_KEY = os.getenv("TIKHUB_API_KEY")
ENABLED = os.getenv("RUN_REAL_TIKHUB_GOLDEN_INTEGRATION") == "YES"
pytestmark = pytest.mark.skipif(
    not (ENABLED and DSN and API_KEY),
    reason="set RUN_REAL_TIKHUB_GOLDEN_INTEGRATION=YES, DATABASE_URL and TIKHUB_API_KEY",
)


def test_real_tikhub_golden_path_is_ledgered_and_queryable() -> None:
    assert DSN and API_KEY
    result = run_live(
        dsn=DSN,
        api_key=API_KEY,
        plan=make_plan(dry_run=False, force_refresh=True),
        triggered_by="pytest-real-golden",
    )
    assert result["observations"] <= 5
    assert result["unique_platform_videos"] <= 5
    assert result["scored_videos"] <= result["unique_platform_videos"]
    assert result["provider_call_count"] <= 2
    assert result["uncached_call_count"] >= 1
    assert result["cached_call_count"] + result["uncached_call_count"] == result["provider_call_count"]

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("select status, output_count from pipeline_run where id=%s", (result["run_id"],))
        assert cur.fetchone()[0] == "success"
        cur.execute(
            """
            select count(*) from external_api_call
            where provider='tikhub' and platform='douyin'
              and started_at >= now() - interval '10 minutes'
            """
        )
        assert cur.fetchone()[0] >= 1
        cur.execute(
            """
            select used_requests from daily_budget
            where budget_date=current_date and provider='tikhub' and budget_key=%s
            """,
            (GOLDEN_BUDGET_KEY,),
        )
        assert cur.fetchone()[0] <= 2
