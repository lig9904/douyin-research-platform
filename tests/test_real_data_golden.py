from __future__ import annotations

import os
from dataclasses import replace
from types import SimpleNamespace

import psycopg
import pytest

import douyin_research.l0l1.real_data as real_data
from douyin_research.l0l1.runner import DiscoverySource, L0L1Runner
from douyin_research.l0l1.real_data import (
    GOLDEN_MAX_EXTERNAL_CALLS,
    GOLDEN_MAX_ITEMS,
    _make_run_limited_before_external_call,
    make_plan,
    plan_dict,
)
from douyin_research.providers.endpoints import get_endpoint
from douyin_research.providers.errors import ProviderBudgetError
from douyin_research.providers.types import ProviderPage


TEST_DSN = os.getenv("TEST_DATABASE_URL")


def test_default_golden_plan_is_dry_run_and_hard_bounded() -> None:
    plan = make_plan()

    assert plan.dry_run is True
    assert plan.max_items == GOLDEN_MAX_ITEMS
    assert plan.max_external_calls == GOLDEN_MAX_EXTERNAL_CALLS
    assert plan.max_cost_usd is None
    assert plan.page == 1
    assert plan.retry_count == 0
    assert plan.force_refresh is False
    assert plan_dict(plan)["source"] == "douyin.billboard.low_fan"
    assert get_endpoint(plan.source).unit_cost_usd == 0.001


def test_force_refresh_is_explicit_in_golden_plan() -> None:
    plan = make_plan(dry_run=False, force_refresh=True)
    assert plan_dict(plan)["force_refresh"] is True


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_items": 0}, "max_items"),
        ({"max_items": GOLDEN_MAX_ITEMS + 1}, "max_items"),
        ({"max_external_calls": 0}, "max_external_calls"),
        ({"max_external_calls": GOLDEN_MAX_EXTERNAL_CALLS + 1}, "max_external_calls"),
        ({"max_external_calls": 1}, "at least 2"),
        ({"page": 2}, "page=1"),
        ({"date_window_hours": 25}, "date_window_hours"),
        ({"max_cost_usd": -0.001}, "max_cost_usd"),
        ({"max_cost_usd": float("nan")}, "max_cost_usd"),
        ({"max_cost_usd": float("inf")}, "max_cost_usd"),
        ({"max_cost_usd": float("-inf")}, "max_cost_usd"),
    ],
)
def test_golden_plan_rejects_invalid_optional_cost_or_pagination_expansion(kwargs, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        make_plan(**kwargs)


def test_run_live_revalidates_directly_constructed_nonfinite_budget(monkeypatch) -> None:
    plan = replace(make_plan(dry_run=False), max_cost_usd=float("nan"))

    def unexpected_transport(*args, **kwargs):
        pytest.fail("transport must not be created for an invalid budget")

    monkeypatch.setattr(real_data, "TikHubTransport", unexpected_transport)
    with pytest.raises(ValueError, match="max_cost must be finite"):
        real_data.run_live(
            dsn="postgresql://invalid.invalid/research",
            api_key="not-used",
            plan=plan,
            triggered_by="test",
        )


def test_no_detail_plan_can_fit_one_external_call() -> None:
    plan = make_plan(enrich_details=False, max_external_calls=1)
    assert plan.enrich_details is False
    assert plan.max_external_calls == 1


def test_run_call_limit_is_independent_from_daily_ledger() -> None:
    reserved = []
    reserve = _make_run_limited_before_external_call(
        reserve_daily_ledger=lambda spec: reserved.append(spec.key),
        max_external_calls=2,
    )
    spec = get_endpoint("douyin.billboard.low_fan")

    reserve(spec)
    reserve(spec)
    with pytest.raises(ProviderBudgetError, match="3>2"):
        reserve(spec)

    assert reserved == [spec.key, spec.key]


def test_rejected_daily_reservation_does_not_consume_run_slot() -> None:
    attempts = []

    def ledger(spec):
        attempts.append(spec.key)
        if len(attempts) == 1:
            raise ProviderBudgetError("configured daily ceiling")

    reserve = _make_run_limited_before_external_call(
        reserve_daily_ledger=ledger, max_external_calls=1,
    )
    spec = get_endpoint("douyin.billboard.low_fan")
    with pytest.raises(ProviderBudgetError, match="configured daily ceiling"):
        reserve(spec)
    reserve(spec)
    with pytest.raises(ProviderBudgetError, match="2>1"):
        reserve(spec)
    assert attempts == [spec.key, spec.key]


@pytest.mark.parametrize("max_external_calls", [False, 0, 3, "2"])
def test_run_call_limit_rejects_directly_constructed_invalid_plan(max_external_calls) -> None:
    plan = replace(make_plan(dry_run=False), max_external_calls=max_external_calls)

    with pytest.raises(ValueError, match="max_external_calls"):
        real_data.run_live(
            dsn="postgresql://invalid.invalid/research",
            api_key="not-used",
            plan=plan,
            triggered_by="test",
        )


def test_successful_run_summarizes_ledger_cost_in_usd(monkeypatch):
    completions = []
    configurations = []

    class Budget:
        def configure(self, **kwargs):
            configurations.append(kwargs)

        def make_before_external_call(self, **kwargs):
            return lambda _: None

    class Store:
        def finish_run(self, run_id, **kwargs):
            completions.append((run_id, kwargs))

    class Runner:
        def __init__(self, **kwargs):
            self.store = kwargs["store"]

        def run(self, *args, **kwargs):
            return SimpleNamespace(run_id="run-1", platform="douyin", source_count=1,
                                   observations=3, unique_platform_videos=3, scores={})

    calls = [SimpleNamespace(estimated_cost=0.001, actual_cost=None, cached=False,
                             metadata={"billing_status": "estimated", "http_attempt_count": 1}),
             SimpleNamespace(estimated_cost=0.050, actual_cost=None, cached=False,
                             metadata={"billing_status": "estimated", "http_attempt_count": 1}),
             SimpleNamespace(estimated_cost=0, actual_cost=0, cached=True,
                             metadata={"billing_status": "known_zero", "http_attempt_count": 0})]
    monkeypatch.setattr(real_data, "DailyBudgetGuard", lambda _: Budget())
    monkeypatch.setattr(real_data, "TikHubTransport", lambda *a, **kw: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(real_data, "_RecordingPostgresProviderStore", lambda _: SimpleNamespace(recorded_calls=calls))
    monkeypatch.setattr(real_data, "TikHubDouyinProvider", lambda **kw: None)
    monkeypatch.setattr(real_data, "L0L1Store", lambda _: Store())
    monkeypatch.setattr(real_data, "L1Scorer", lambda _: None)
    monkeypatch.setattr(real_data, "L0L1Runner", Runner)
    result = real_data.run_live(dsn="test", api_key="test", plan=make_plan(dry_run=False), triggered_by="test")
    assert configurations == [{
        "provider": "tikhub",
        "budget_key": "golden-local",
        "max_requests": None,
        "max_cost": None,
        "cost_currency": "USD",
    }]
    assert result["cached_call_count"] == 1
    assert result["uncached_call_count"] == 2
    assert completions[0][1]["api_cost"] == pytest.approx(0.051)
    assert completions[0][1]["cost_currency"] == "USD"
    assert completions[0][1]["summary"]["api_cost_basis"] == "estimated"
    assert result["cost_summary"]["reconciled_api_cost_usd"] is None
    assert result["cost_summary"]["observed_http_attempts"] == 2


def test_provider_budget_mode_does_not_double_reserve_runner_calls() -> None:
    class Budget:
        calls: list[dict] = []

        def acquire(self, **kwargs) -> None:
            self.calls.append(kwargs)

        def refund(self, **kwargs) -> None:
            self.calls.append(kwargs)

    class Store:
        def create_run(self, *args, **kwargs):
            return "run-1"

        def ingest(self, *args, **kwargs):
            return None

        def finish_run(self, *args, **kwargs):
            return None

    class Scorer:
        def score_run(self, run_id):
            return {}

    class Provider:
        provider_name = "tikhub"
        platform_name = "douyin"
        video_batch_size = 50

        def discover(self, kind, **kwargs):
            return ProviderPage(items=[], endpoint_key="test", request_fingerprint="fp", cached=False)

        def fetch_videos(self, ids):
            return []

    budget = Budget()
    result = L0L1Runner(
        provider=Provider(), store=Store(), scorer=Scorer(), budget=budget,
        provider_reserves_budget=True,
    ).run([DiscoverySource("low_fan", "test", "test")])

    assert result.observations == 0
    assert budget.calls == []


@pytest.mark.skipif(not TEST_DSN, reason="TEST_DATABASE_URL not configured")
def test_two_same_day_golden_batches_keep_run_cap_and_accumulate_daily_ledger() -> None:
    """Exercise the real ledger without a transport or provider call."""
    assert TEST_DSN
    with psycopg.connect(TEST_DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "delete from daily_budget where provider='tikhub' and budget_key=%s",
            (real_data.GOLDEN_BUDGET_KEY,),
        )
        conn.commit()

    # Reproduce the observed stale row: it has already consumed its old
    # two-request daily maximum before the next operator batch begins.
    stale_budget = real_data.DailyBudgetGuard(TEST_DSN)
    stale_budget.configure(
        provider="tikhub", budget_key=real_data.GOLDEN_BUDGET_KEY,
        max_requests=2, max_cost=0.1,
    )
    stale_budget.acquire(
        provider="tikhub", budget_key=real_data.GOLDEN_BUDGET_KEY,
        requests=2, estimated_cost=0.0,
    )
    # Two hooks model two independent operator batches.  Each gets a fresh
    # two-call allowance while both write into the same durable day ledger.
    spec = get_endpoint("douyin.billboard.low_fan")
    first_batch = _make_run_limited_before_external_call(
        reserve_daily_ledger=stale_budget.make_before_external_call(
            provider="tikhub", budget_key=real_data.GOLDEN_BUDGET_KEY,
        ),
        max_external_calls=2,
    )
    second_batch = _make_run_limited_before_external_call(
        reserve_daily_ledger=stale_budget.make_before_external_call(
            provider="tikhub", budget_key=real_data.GOLDEN_BUDGET_KEY,
        ),
        max_external_calls=2,
    )
    for batch in (first_batch, second_batch):
        # Mirror run_live's configuration step before every batch; it must
        # remove the obsolete ceiling without resetting historical usage.
        stale_budget.configure(
            provider="tikhub", budget_key=real_data.GOLDEN_BUDGET_KEY,
            max_requests=None, max_cost=None,
        )
        batch(spec)
        batch(spec)
        with pytest.raises(ProviderBudgetError, match="3>2"):
            batch(spec)

    with psycopg.connect(TEST_DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select max_requests, max_cost, used_requests, spent_cost
            from daily_budget
            where budget_date=current_date and provider='tikhub' and budget_key=%s
            """,
            (real_data.GOLDEN_BUDGET_KEY,),
        )
        row = cur.fetchone()
        assert row[:3] == (None, None, 6)
        assert float(row[3]) == pytest.approx(4 * spec.unit_cost_usd)
