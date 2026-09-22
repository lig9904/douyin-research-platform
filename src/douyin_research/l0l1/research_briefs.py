"""Validated user-authored discovery plans for the research control plane.

The brief controls collection scope only.  It never creates ASR or L3
approvals and never submits content to an AI provider.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from douyin_research.providers.endpoints import EndpointSpec
from douyin_research.providers.errors import ProviderBudgetError
from douyin_research.providers.store import PostgresProviderStore
from douyin_research.providers.tikhub_provider import TikHubDouyinProvider
from douyin_research.providers.transport import TikHubTransport
from douyin_research.providers.types import ProviderCallMeta

from .budget import DailyBudgetGuard
from .ingest import L0L1Store
from .runner import DiscoverySource, L0L1Runner
from .scoring import L1Scorer


RESEARCH_BRIEF_BUDGET_KEY = "research-brief"
RESEARCH_BRIEF_MAX_EXTERNAL_CALLS = 2
_SOURCE_TYPES = frozenset({"low_fan", "keyword", "account"})
_DEPTHS = frozenset({"metadata", "comments", "media", "review_ready"})
_WINDOWS = frozenset({24, 72, 168, 720})
_CADENCES = frozenset({6, 12, 24})


@dataclass(frozen=True, slots=True)
class ResearchBriefConfig:
    platform: str
    source_type: str
    target: str | None
    time_window_hours: int
    max_items: int
    depth: str
    cadence_hours: int | None


class _RecordingProviderStore(PostgresProviderStore):
    def __init__(self, dsn: str) -> None:
        super().__init__(dsn)
        self.recorded_calls: list[ProviderCallMeta] = []

    def record_call(self, call: ProviderCallMeta) -> None:
        self.recorded_calls.append(call)
        super().record_call(call)


def _integer(name: str, value: object, allowed: frozenset[int]) -> int:
    if type(value) is not int or value not in allowed:
        raise ValueError(f"{name} is invalid")
    return value


def validate_config(config: ResearchBriefConfig) -> ResearchBriefConfig:
    if config.platform != "douyin":
        raise ValueError("platform must be douyin")
    if config.source_type not in _SOURCE_TYPES:
        raise ValueError("source_type is invalid")
    if config.depth not in _DEPTHS:
        raise ValueError("depth is invalid")
    _integer("time_window_hours", config.time_window_hours, _WINDOWS)
    if config.cadence_hours is not None:
        _integer("cadence_hours", config.cadence_hours, _CADENCES)
    if type(config.max_items) is not int or not 1 <= config.max_items <= 20:
        raise ValueError("max_items is invalid")
    if config.depth in {"media", "review_ready"} and config.max_items > 5:
        raise ValueError("media scope must not exceed 5 items")
    if config.source_type == "low_fan":
        if config.target is not None:
            raise ValueError("low_fan target must be empty")
        if config.max_items > 5 or config.time_window_hours == 720:
            raise ValueError("low_fan scope exceeds its bounded endpoint")
    else:
        if not isinstance(config.target, str):
            raise ValueError("target is required")
        normalized = " ".join(config.target.strip().split())
        if not normalized or len(normalized) > 120 or "\x00" in normalized:
            raise ValueError("target is invalid")
        if normalized != config.target:
            raise ValueError("target must be normalized")
    return config


def make_config(
    *,
    platform: str = "douyin",
    source_type: str,
    target: str | None,
    time_window_hours: int,
    max_items: int,
    depth: str,
    cadence_hours: int | None,
) -> ResearchBriefConfig:
    normalized_target = None
    if source_type != "low_fan" and isinstance(target, str):
        normalized_target = " ".join(target.strip().split())
    return validate_config(
        ResearchBriefConfig(
            platform=platform,
            source_type=source_type,
            target=normalized_target,
            time_window_hours=time_window_hours,
            max_items=max_items,
            depth=depth,
            cadence_hours=cadence_hours,
        )
    )


def config_snapshot(config: ResearchBriefConfig) -> dict[str, Any]:
    """Durable server-side snapshot; never returned in paid job summaries."""
    validate_config(config)
    return asdict(config)


def discovery_source(
    config: ResearchBriefConfig, *, now: datetime | None = None,
) -> DiscoverySource:
    validate_config(config)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    cutoff = current - timedelta(hours=config.time_window_hours)
    target_digest = hashlib.sha256((config.target or "").encode("utf-8")).hexdigest()[:16]
    source_key = (
        f"brief:{config.source_type}:{target_digest}:{config.time_window_hours}h"
    )
    common = {
        "source_type": f"brief_{config.source_type}",
        "source_key": source_key,
        "max_items": config.max_items,
        "published_after": cutoff,
    }
    if config.source_type == "low_fan":
        return DiscoverySource(
            kind="low_fan",
            kwargs={
                "page": 1,
                "page_size": config.max_items,
                "date_window": config.time_window_hours,
                "tags": [],
                "force_refresh": False,
            },
            **common,
        )
    if config.source_type == "keyword":
        return DiscoverySource(
            kind="search",
            kwargs={"query": config.target, "force_refresh": False},
            **common,
        )
    return DiscoverySource(
        kind="account_posts",
        kwargs={
            "account_id": config.target,
            "count": config.max_items,
            "force_refresh": False,
        },
        **common,
    )


def depth_plan(depth: str) -> dict[str, bool]:
    if depth not in _DEPTHS:
        raise ValueError("depth is invalid")
    return {
        "collect_comments": depth in {"comments", "media", "review_ready"},
        "collect_media": depth in {"media", "review_ready"},
        "review_required": depth == "review_ready",
        "auto_submit_asr": False,
        "auto_submit_l3": False,
    }


def _run_call_gate(
    daily_reserve: Callable[[EndpointSpec], None],
) -> Callable[[EndpointSpec], None]:
    count = 0

    def reserve(spec: EndpointSpec) -> None:
        nonlocal count
        if count >= RESEARCH_BRIEF_MAX_EXTERNAL_CALLS:
            raise ProviderBudgetError("research brief external-call limit exceeded")
        daily_reserve(spec)
        count += 1

    return reserve


def run_live(
    *, dsn: str, api_key: str, config: ResearchBriefConfig, triggered_by: str,
) -> dict[str, Any]:
    validate_config(config)
    if not dsn or not api_key or not triggered_by:
        raise ValueError("database, API key and service identity are required")

    budget = DailyBudgetGuard(dsn)
    budget.configure(
        provider="tikhub",
        budget_key=RESEARCH_BRIEF_BUDGET_KEY,
        max_requests=None,
        max_cost=None,
        cost_currency="USD",
    )
    transport = TikHubTransport(api_key, max_retries=0)
    provider_store = _RecordingProviderStore(dsn)
    provider = TikHubDouyinProvider(
        transport=transport,
        store=provider_store,
        auth_scope="research-brief-v1",
        before_external_call=_run_call_gate(
            budget.make_before_external_call(
                provider="tikhub", budget_key=RESEARCH_BRIEF_BUDGET_KEY,
            )
        ),
        detail_strategy="batch50",
    )
    store = L0L1Store(dsn)
    runner = L0L1Runner(
        provider=provider,
        store=store,
        scorer=L1Scorer(dsn),
        budget=budget,
        budget_key=RESEARCH_BRIEF_BUDGET_KEY,
        provider_reserves_budget=True,
    )
    try:
        summary = runner.run(
            [discovery_source(config)],
            enrich_details=True,
            triggered_by=triggered_by,
            enrich_new_only=True,
        )
    finally:
        transport.close()

    calls = provider_store.recorded_calls
    cached_calls = sum(call.cached for call in calls)
    uncached_calls = len(calls) - cached_calls
    estimated_cost = sum(float(call.estimated_cost or 0) for call in calls)
    unknown_cost_calls = sum(
        call.metadata.get("billing_status") == "unknown" for call in calls
    )
    cost_summary = {
        "estimated_api_cost_usd": estimated_cost,
        "reconciled_api_cost_usd": None,
        "unknown_cost_calls": unknown_cost_calls,
        "api_cost_basis": "unknown" if unknown_cost_calls else "estimated",
    }
    store.finish_run(
        summary.run_id,
        api_cost=estimated_cost,
        cost_currency="USD",
        summary=cost_summary,
    )
    return {
        "run_id": str(summary.run_id),
        "platform": summary.platform,
        "observations": summary.observations,
        "unique_platform_videos": summary.unique_platform_videos,
        "new_candidate_count": summary.new_candidate_count,
        "scored_videos": len(summary.scores),
        "provider_call_count": len(calls),
        "cached_call_count": cached_calls,
        "uncached_call_count": uncached_calls,
        "max_external_calls": RESEARCH_BRIEF_MAX_EXTERNAL_CALLS,
        "sdk_retries": 0,
        "cost_summary": cost_summary,
        **depth_plan(config.depth),
    }
