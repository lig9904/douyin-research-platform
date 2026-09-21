"""Strictly bounded, opt-in TikHub-to-L0/L1 local intake.

This module is deliberately small: it is an operator entrypoint, not a new
collector framework.  It reuses the provider cache/call ledger, canonical
normalizer and L0/L1 runner already used by the application.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Callable

from douyin_research.providers.store import PostgresProviderStore
from douyin_research.providers.types import ProviderCallMeta
from douyin_research.providers.tikhub_provider import TikHubDouyinProvider
from douyin_research.providers.transport import TikHubTransport
from douyin_research.providers.video_fetch_plan import plan_video_fetches
from douyin_research.providers.endpoints import EndpointSpec
from douyin_research.providers.errors import ProviderBudgetError

from .budget import DailyBudgetGuard
from .ingest import L0L1Store
from .runner import DiscoverySource, L0L1Runner
from .scoring import L1Scorer


GOLDEN_BUDGET_KEY = "golden-local"
GOLDEN_MAX_ITEMS = 5
GOLDEN_MAX_EXTERNAL_CALLS = 2
GOLDEN_RUN_VERSION = "golden-local-v1"
_DETAIL_STRATEGIES = frozenset(("batch50", "cost_aware"))


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
    detail_strategy: str = "batch50"


class _RecordingPostgresProviderStore(PostgresProviderStore):
    """Persist calls normally while retaining this process's aggregate proof."""

    def __init__(self, dsn: str) -> None:
        super().__init__(dsn)
        self.recorded_calls: list[ProviderCallMeta] = []

    def record_call(self, call: ProviderCallMeta) -> None:
        self.recorded_calls.append(call)
        super().record_call(call)


def _call_limit_for_strategy(detail_strategy: str) -> int:
    """Return the run-local call cap for a supported detail strategy."""
    if not isinstance(detail_strategy, str) or detail_strategy not in _DETAIL_STRATEGIES:
        raise ValueError("detail_strategy must be batch50 or cost_aware")
    return (
        GOLDEN_MAX_EXTERNAL_CALLS
        if detail_strategy == "batch50"
        else GOLDEN_MAX_ITEMS + 1
    )


def _validate_non_boolean_int(name: str, value: Any, *, minimum: int, maximum: int) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise ValueError(
            f"{name} must be a non-boolean integer between {minimum} and {maximum}"
        )


def make_plan(
    *,
    dry_run: bool = True,
    max_items: int = GOLDEN_MAX_ITEMS,
    max_external_calls: int | None = None,
    max_cost_usd: float | None = None,
    page: int = 1,
    date_window_hours: int = 24,
    enrich_details: bool = True,
    force_refresh: bool = False,
    detail_strategy: str = "batch50",
) -> GoldenIntakePlan:
    """Validate a conservative one-page collection plan before touching a secret."""
    _validate_non_boolean_int("max_items", max_items, minimum=1, maximum=GOLDEN_MAX_ITEMS)
    call_limit = _call_limit_for_strategy(detail_strategy)
    detail_requests = plan_video_fetches(
        [str(i) for i in range(max_items)], strategy=detail_strategy,
    )
    needed_calls = 1 + (len(detail_requests) if enrich_details else 0)
    if max_external_calls is None:
        max_external_calls = needed_calls
    _validate_non_boolean_int(
        "max_external_calls", max_external_calls, minimum=1, maximum=call_limit,
    )
    if max_external_calls < needed_calls:
        raise ValueError(
            f"max_external_calls must be at least {needed_calls} when "
            f"enrich_details={enrich_details}"
        )
    if max_cost_usd is not None and (
        isinstance(max_cost_usd, bool)
        or not isinstance(max_cost_usd, (int, float))
        or not math.isfinite(max_cost_usd)
        or max_cost_usd < 0
    ):
        raise ValueError("max_cost_usd must be finite and non-negative or None")
    if page != 1:
        # The low-fan billboard has page numbering rather than a reliable
        # continuation cursor.  A golden run is intentionally one page; this
        # rejects accidental pagination expansion before it can spend money.
        raise ValueError("golden intake only permits page=1")
    if date_window_hours not in {1, 24, 72, 168}:
        raise ValueError("date_window_hours must be one of 1, 24, 72, 168")
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
        detail_strategy=detail_strategy,
    )


def plan_dict(plan: GoldenIntakePlan) -> dict[str, Any]:
    """A secret- and content-free plan representation for CLI/tests."""
    return asdict(plan)


def _make_run_limited_before_external_call(
    *,
    reserve_daily_ledger: Callable[[EndpointSpec], None],
    max_external_calls: int,
    detail_strategy: str = "batch50",
) -> Callable[[EndpointSpec], None]:
    """Combine the per-run call envelope with the unlimited daily ledger.

    ``daily_budget`` is an accounting row shared by every golden run on a
    date, so its ``max_requests`` must not be used for one run's call cap.
    The provider invokes this hook only for uncached calls, which makes this
    small in-process counter the exact limit for the current execution while
    the delegated hook continues to atomically record daily request/cost use.
    """
    call_limit = _call_limit_for_strategy(detail_strategy)
    _validate_non_boolean_int(
        "max_external_calls", max_external_calls, minimum=1, maximum=call_limit,
    )
    reserved_calls = 0

    def reserve(spec: EndpointSpec) -> None:
        nonlocal reserved_calls
        if reserved_calls >= max_external_calls:
            raise ProviderBudgetError(
                "golden run external-call limit exceeded: "
                f"{reserved_calls + 1}>{max_external_calls}"
            )
        # Do not consume the run allowance if the durable ledger rejects this
        # call (for example, an explicit operator cost ceiling).
        reserve_daily_ledger(spec)
        reserved_calls += 1

    return reserve


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
    # ``GoldenIntakePlan`` is public and frozen but can still be constructed
    # directly; re-check the paid-call envelope before changing its ledger.
    _validate_non_boolean_int(
        "max_items", plan.max_items, minimum=1, maximum=GOLDEN_MAX_ITEMS,
    )
    call_limit = _call_limit_for_strategy(plan.detail_strategy)
    _validate_non_boolean_int(
        "max_external_calls", plan.max_external_calls, minimum=1, maximum=call_limit,
    )
    detail_requests = plan_video_fetches(
        [str(i) for i in range(plan.max_items)], strategy=plan.detail_strategy,
    )
    needed_calls = 1 + (len(detail_requests) if plan.enrich_details else 0)
    if plan.max_external_calls < needed_calls:
        raise ValueError(
            f"max_external_calls must be at least {needed_calls} when "
            f"enrich_details={plan.enrich_details}"
        )
    if plan.max_cost_usd is not None and (
        isinstance(plan.max_cost_usd, bool)
        or not isinstance(plan.max_cost_usd, (int, float))
        or not math.isfinite(plan.max_cost_usd)
        or plan.max_cost_usd < 0
    ):
        raise ValueError("max_cost must be finite and non-negative or None")

    budget = DailyBudgetGuard(dsn)
    budget.configure(
        provider="tikhub",
        budget_key=GOLDEN_BUDGET_KEY,
        # This row is the all-day cost/request ledger, not a run-local quota.
        # Keep it uncapped by default; the bounded hook below enforces the
        # plan's call envelope without old runs blocking a new batch.
        max_requests=None,
        max_cost=plan.max_cost_usd,
        cost_currency="USD",
    )
    transport = TikHubTransport(api_key, max_retries=0)
    provider_store = _RecordingPostgresProviderStore(dsn)
    provider = TikHubDouyinProvider(
        transport=transport,
        store=provider_store,
        auth_scope="golden-local-v1",
        before_external_call=_make_run_limited_before_external_call(
            reserve_daily_ledger=budget.make_before_external_call(
                provider="tikhub", budget_key=GOLDEN_BUDGET_KEY
            ),
            max_external_calls=plan.max_external_calls,
            detail_strategy=plan.detail_strategy,
        ),
        detail_strategy=plan.detail_strategy,
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
    # Preserve the known quote subtotal without relabeling it supplier spend.
    calls = provider_store.recorded_calls
    estimated_cost = sum(float(call.estimated_cost or 0) for call in calls)
    unknown_cost_calls = sum(call.metadata.get("billing_status") == "unknown" for call in calls)
    http_attempts = [call.metadata.get("http_attempt_count") for call in calls]
    cost_summary = {
        "estimated_api_cost_usd": estimated_cost,
        "reconciled_api_cost_usd": None,
        "unknown_cost_calls": unknown_cost_calls,
        "observed_http_attempts": sum(n for n in http_attempts if n is not None),
        "opaque_attempt_calls": sum(n is None for n in http_attempts),
        "api_cost_basis": "unknown" if unknown_cost_calls else "estimated",
    }
    runner.store.finish_run(
        summary.run_id,
        api_cost=estimated_cost,
        cost_currency="USD",
        summary=cost_summary,
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
        "detail_strategy": plan.detail_strategy,
        "cost_summary": cost_summary,
    }
