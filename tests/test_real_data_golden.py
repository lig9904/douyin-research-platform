from __future__ import annotations

import pytest

from douyin_research.l0l1.runner import DiscoverySource, L0L1Runner
from douyin_research.l0l1.real_data import (
    GOLDEN_MAX_EXTERNAL_CALLS,
    GOLDEN_MAX_ITEMS,
    make_plan,
    plan_dict,
)
from douyin_research.providers.types import ProviderPage


def test_default_golden_plan_is_dry_run_and_hard_bounded() -> None:
    plan = make_plan()

    assert plan.dry_run is True
    assert plan.max_items == GOLDEN_MAX_ITEMS
    assert plan.max_external_calls == GOLDEN_MAX_EXTERNAL_CALLS
    assert plan.page == 1
    assert plan.retry_count == 0
    assert plan_dict(plan)["source"] == "douyin.billboard.low_fan"


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
    ],
)
def test_golden_plan_rejects_spend_or_pagination_expansion(kwargs, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        make_plan(**kwargs)


def test_no_detail_plan_can_fit_one_external_call() -> None:
    plan = make_plan(enrich_details=False, max_external_calls=1)
    assert plan.enrich_details is False
    assert plan.max_external_calls == 1


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
