"""Strictly bounded, opt-in TikHub-to-L0/L1 local intake.

This module is deliberately small: it is an operator entrypoint, not a new
collector framework.  It reuses the provider cache/call ledger, canonical
normalizer and L0/L1 runner already used by the application.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

from douyin_research.providers.store import PostgresProviderStore
from douyin_research.providers.types import ProviderCallMeta
from douyin_research.providers.tikhub_provider import TikHubDouyinProvider
from douyin_research.providers.transport import TikHubTransport

from .budget import DailyBudgetGuard
from .ingest import L0L1Store
from .runner import DiscoverySource, L0L1Runner
from .scoring import L1Scorer


GOLDEN_BUDGET_KEY = "golden-local"
GOLDEN_MAX_ITEMS = 5
GOLDEN_MAX_EXTERNAL_CALLS = 2
GOLDEN_RUN_VERSION = "golden-local-v1"


@dataclass(frozen=True, slots=True)
class GoldenIntakePlan:
    """The entire paid-call envelope, safe to print or persist in documentation."""

    dry_run: bool
    max_items: int
    max_external_calls: int
    max_cost_usd: float | None
    source: str
    page: int
    date_window_hours: int
    enrich_details: bool
    retry_count: int
    force_refresh: bool


class _RecordingPostgresProviderStore(PostgresProviderStore):
    """Persist calls normally while retaining this process's aggregate proof."""

    def __init__(self, dsn: str) -> None:
        super().__init__(dsn)
        self.recorded_calls: list[ProviderCallMeta] = []

    def record_call(self, call: ProviderCallMeta) -> None:
        self.recorded_calls.append(call)
        super().record_call(call)


def make_plan(
    *,
    dry_run: bool = True,
    max_items: int = GOLDEN_MAX_ITEMS,
    max_external_calls: int = GOLDEN_MAX_EXTERNAL_CALLS,
    max_cost_usd: float | None = None,
    page: int = 1,
    date_window_hours: int = 24,
    enrich_details: bool = True,
    force_refresh: bool = False,
) -> GoldenIntakePlan:
    """Validate a conservative one-page collection plan before touching a secret."""
    if not 1 <= max_items <= GOLDEN_MAX_ITEMS:
        raise ValueError(f"max_items must be between 1 and {GOLDEN_MAX_ITEMS}")
    if not 1 <= max_external_calls <= GOLDEN_MAX_EXTERNAL_CALLS:
        raise ValueError(
            f"max_external_calls must be between 1 and {GOLDEN_MAX_EXTERNAL_CALLS}"
        )
    needed_calls = 1 + int(enrich_details)
    if max_external_calls < needed_calls:
        raise ValueError(
            f"max_external_calls must be at least {needed_calls} when "
            f"enrich_details={enrich_details}"
        )
    if max_cost_usd is not None and (
        not math.isfinite(max_cost_usd) or max_cost_usd < 0
    ):
        raise ValueError("max_cost_usd must be finite and non-negative or None")
    if page != 1:
        # The low-fan billboard has page numbering rather than a reliable
        # continuation cursor.  A golden run is intentionally one page; this
        # rejects accidental pagination expansion before it can spend money.
        raise ValueError("golden intake only permits page=1")
    if not 1 <= date_window_hours <= 24:
        raise ValueError("date_window_hours must be between 1 and 24")
    return GoldenIntakePlan(
        dry_run=dry_run,
        max_items=max_items,
        max_external_calls=max_external_calls,
        max_cost_usd=max_cost_usd,
        source="douyin.billboard.low_fan",
        page=page,
        date_window_hours=date_window_hours,
        enrich_details=enrich_details,
        retry_count=0,
        force_refresh=force_refresh,
    )


def plan_dict(plan: GoldenIntakePlan) -> dict[str, Any]:
    """A secret- and content-free plan representation for CLI/tests."""
    return asdict(plan)


def run_live(*, dsn: str, api_key: str, plan: GoldenIntakePlan, triggered_by: str) -> dict[str, Any]:
    """Execute the one-page plan with ledger-backed exact external-call gating.

    The provider hook reserves each uncached call before transport.  Cached
    responses therefore consume neither budget nor paid-call quota.  The
    runner is told not to make its legacy source-sized reservations, avoiding
    double booking.  Canonical uniqueness remains `(platform, platform_video_id)`.
    """
    if plan.dry_run:
        raise ValueError("run_live requires a plan with dry_run=False")
    if not dsn:
        raise ValueError("database DSN is required")
    if not api_key:
        raise ValueError("TikHub API key is required")

    budget = DailyBudgetGuard(dsn)
    budget.configure(
        provider="tikhub",
        budget_key=GOLDEN_BUDGET_KEY,
        max_requests=plan.max_external_calls,
        max_cost=plan.max_cost_usd,
        cost_currency="USD",
    )
    transport = TikHubTransport(api_key, max_retries=0)
    provider_store = _RecordingPostgresProviderStore(dsn)
    provider = TikHubDouyinProvider(
        transport=transport,
        store=provider_store,
        auth_scope="golden-local-v1",
        before_external_call=budget.make_before_external_call(
            provider="tikhub", budget_key=GOLDEN_BUDGET_KEY
        ),
    )
    runner = L0L1Runner(
        provider=provider,
        store=L0L1Store(dsn),
        scorer=L1Scorer(dsn),
        budget=budget,
        budget_key=GOLDEN_BUDGET_KEY,
        provider_reserves_budget=True,
    )
    try:
        summary = runner.run(
            [
                DiscoverySource(
                    kind="low_fan",
                    source_type="golden_low_fan",
                    source_key=f"{plan.date_window_hours}h-page-{plan.page}",
                    kwargs={
                        "page": plan.page,
                        "page_size": plan.max_items,
                        "date_window": plan.date_window_hours,
                        "tags": [],
                        "force_refresh": plan.force_refresh,
                    },
                    max_items=plan.max_items,
                )
            ],
            enrich_details=plan.enrich_details,
            triggered_by=triggered_by,
        )
    finally:
        transport.close()

    # Do not return raw provider payloads, video text, request IDs or secrets.
    cached_calls = sum(1 for call in provider_store.recorded_calls if call.cached)
    uncached_calls = sum(1 for call in provider_store.recorded_calls if not call.cached)
    # The business run and provider ledger must use the same amount/currency.
    runner.store.finish_run(
        summary.run_id,
        api_cost=sum(float(call.actual_cost or 0) for call in provider_store.recorded_calls),
        cost_currency="USD",
    )
    return {
        "run_id": str(summary.run_id),
        "platform": summary.platform,
        "source_count": summary.source_count,
        "observations": summary.observations,
        "unique_platform_videos": summary.unique_platform_videos,
        "scored_videos": len(summary.scores),
        "budget_key": GOLDEN_BUDGET_KEY,
        "max_external_calls": plan.max_external_calls,
        "retry_count": plan.retry_count,
        "provider_call_count": len(provider_store.recorded_calls),
        "cached_call_count": cached_calls,
        "uncached_call_count": uncached_calls,
    }
