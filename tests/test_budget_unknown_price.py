from __future__ import annotations

import os
from dataclasses import replace
from uuid import uuid4

import psycopg
import pytest

from douyin_research.l0l1.budget import DailyBudgetGuard
from douyin_research.mcp.readonly import CanonicalResearchQueries
from douyin_research.providers.endpoints import get_endpoint
from douyin_research.providers.errors import ProviderBudgetError


DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="isolated TEST_DATABASE_URL required")


@pytest.mark.parametrize("ceiling", [None, 1.0])
def test_unknown_price_is_counted_without_assuming_zero_or_bypassing_ceiling(ceiling):
    key = f"unknown-price-{uuid4()}"
    guard = DailyBudgetGuard(DSN)
    guard.configure(provider="tikhub", budget_key=key, max_requests=None, max_cost=ceiling)
    hook = guard.make_before_external_call(provider="tikhub", budget_key=key)
    spec = replace(get_endpoint("douyin.app.one_video"), unit_cost_usd=None)
    try:
        if ceiling is None:
            hook(spec)
            expected = (1, 1, 0)
        else:
            with pytest.raises(ProviderBudgetError, match="unknown price"):
                hook(spec)
            expected = (0, 0, 0)
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute("select used_requests, unknown_price_requests, spent_cost from daily_budget where budget_key=%s", (key,))
            assert cur.fetchone() == expected
        rows = CanonicalResearchQueries(DSN).get_cost_summary()["daily_budgets"]
        row = next(row for row in rows if row["budget_key"] == key)
        assert row["unknown_price_requests"] == expected[1]
        assert row["spent_cost_basis"] == "known_estimated_reservation_not_supplier_bill"
        if ceiling is None:
            guard.refund(provider="tikhub", budget_key=key, estimated_cost=None)
            with psycopg.connect(DSN) as conn, conn.cursor() as cur:
                cur.execute("select used_requests, unknown_price_requests, spent_cost from daily_budget where budget_key=%s", (key,))
                assert cur.fetchone() == (0, 0, 0)
    finally:
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute("delete from daily_budget where budget_key=%s", (key,))
