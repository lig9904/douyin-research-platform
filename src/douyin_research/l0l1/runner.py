"""Bounded multi-source L0/L1 orchestration. No LLM calls."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from douyin_research.providers.contracts import PlatformResearchProvider
from douyin_research.providers.types import ProviderPage, VideoObservation

from .budget import DailyBudgetGuard
from .ingest import DiscoveryContext, L0L1Store
from .scoring import L1Scorer


@dataclass(slots=True)
class DiscoverySource:
    kind: str
    source_type: str
    source_key: str
    kwargs: dict[str, Any] = field(default_factory=dict)
    max_items: int = 50


@dataclass(slots=True)
class RunSummary:
    run_id: Any
    platform: str
    source_count: int
    observations: int
    unique_platform_videos: int
    scores: dict[Any, float]
    new_candidate_count: int = 0


class L0L1Runner:
    def __init__(self, *, provider: PlatformResearchProvider, store: L0L1Store,
                 scorer: L1Scorer, budget: DailyBudgetGuard,
                 budget_key: str = "l0l1",
                 provider_reserves_budget: bool = False) -> None:
        self.provider = provider
        self.store = store
        self.scorer = scorer
        self.budget = budget
        self.budget_key = budget_key
        # Legacy callers reserve a source-sized request before each fetch.
        # Real providers can instead reserve exactly once per uncached HTTP
        # call through their before_external_call hook.  Do not use both: that
        # would double-count a paid request and makes cache accounting wrong.
        self.provider_reserves_budget = provider_reserves_budget

    def run(self, sources: list[DiscoverySource], *, enrich_details: bool = True,
            triggered_by: str = "system", enrich_new_only: bool = False) -> RunSummary:
        run_id = self.store.create_run(
            "l0l1_discovery",
            "v1.0.0",
            triggered_by,
            platform=self.provider.platform_name,
        )
        observation_count = 0
        unique_platform_ids: dict[str, VideoObservation] = {}
        new_video_ids: set[Any] = set()
        new_platform_video_ids: set[str] = set()
        detail_enriched_count = 0
        try:
            for source in sources:
                if not self.provider_reserves_budget:
                    self.budget.acquire(
                        provider=self.provider.provider_name,
                        budget_key=self.budget_key,
                        requests=1,
                    )
                page = self._fetch(source)
                if page.cached and not self.provider_reserves_budget:
                    self.budget.refund(
                        provider=self.provider.provider_name,
                        budget_key=self.budget_key,
                        requests=1,
                    )
                items = page.items[: max(0, source.max_items)]
                self._validate_platform(items)
                ranks = {
                    item.video.platform_video_id: idx
                    for idx, item in enumerate(items, start=1)
                }
                ingested = self.store.ingest(
                    items,
                    DiscoveryContext(
                        run_id=run_id,
                        source_type=source.source_type,
                        source_key=source.source_key,
                        provider=self.provider.provider_name,
                        request_fingerprint=page.request_fingerprint,
                        source_count=len(page.items),
                        ranks=ranks,
                    ),
                )
                new_video_ids.update(ingested.new_video_ids)
                new_platform_video_ids.update(ingested.new_platform_video_ids)
                observation_count += len(items)
                for item in items:
                    key = f"{item.video.platform}:{item.video.platform_video_id}"
                    unique_platform_ids[key] = item

            detail_platform_video_ids = (
                new_platform_video_ids
                if enrich_new_only
                else {
                    item.video.platform_video_id
                    for item in unique_platform_ids.values()
                }
            )
            if enrich_details and detail_platform_video_ids:
                # Existing videos still retain this run's discovery and metric
                # evidence, but detail enrichment is paid and only useful for
                # candidates newly introduced to the research corpus.
                ids = [
                    item.video.platform_video_id
                    for item in unique_platform_ids.values()
                    if item.video.platform_video_id in detail_platform_video_ids
                ]
                if not self.provider_reserves_budget:
                    planner = getattr(self.provider, "plan_videos", None)
                    request_count = (len(planner(ids)) if planner is not None else
                                     math.ceil(len(ids) / self.provider.video_batch_size))
                    self.budget.acquire(
                        provider=self.provider.provider_name,
                        budget_key=self.budget_key,
                        requests=request_count,
                    )
                details = self.provider.fetch_videos(ids)
                self._validate_platform(details)
                detail_enriched_count = len(details)
                self.store.ingest(
                    details,
                    DiscoveryContext(
                        run_id=run_id,
                        source_type="detail_enrichment",
                        source_key="batch_video_detail",
                        provider=self.provider.provider_name,
                        request_fingerprint=f"detail:{run_id}",
                        source_count=len(details),
                        record_discovery=False,
                    ),
                )

            scores = self.scorer.score_run(run_id)
            self.store.set_new_candidate_flags(run_id, new_video_ids)
            self.store.finish_run(
                run_id,
                input_count=observation_count,
                output_count=len(unique_platform_ids),
                promoted_l1_count=len(scores),
                summary={"source_count": len(sources), "llm_calls": 0,
                         "platform": self.provider.platform_name,
                         "enrich_details": enrich_details,
                         "enrich_new_only": enrich_new_only,
                         "new_candidate_count": len(new_video_ids),
                         "detail_enriched_count": detail_enriched_count},
            )
            return RunSummary(
                run_id=run_id,
                platform=self.provider.platform_name,
                source_count=len(sources),
                observations=observation_count,
                unique_platform_videos=len(unique_platform_ids),
                scores=scores,
                new_candidate_count=len(new_video_ids),
            )
        except Exception as exc:
            self.store.finish_run(
                run_id,
                status="failed",
                summary={"llm_calls": 0, "error_type": type(exc).__name__},
            )
            raise

    def _fetch(self, source: DiscoverySource) -> ProviderPage[VideoObservation]:
        if source.kind == "search":
            query = source.kwargs.get("query")
            if not query:
                raise ValueError("search source requires kwargs.query")
            kwargs = {k: v for k, v in source.kwargs.items() if k != "query"}
            return self.provider.search_videos(query, **kwargs)
        return self.provider.discover(source.kind, **source.kwargs)

    def _validate_platform(self, items: list[VideoObservation]) -> None:
        mismatched = sorted({
            item.video.platform
            for item in items
            if item.video.platform != self.provider.platform_name
        })
        if mismatched:
            raise ValueError(
                f"provider platform={self.provider.platform_name} returned "
                f"mismatched platforms={mismatched}"
            )
