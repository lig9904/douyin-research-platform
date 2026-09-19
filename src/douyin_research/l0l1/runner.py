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


class L0L1Runner:
    def __init__(self, *, provider: PlatformResearchProvider, store: L0L1Store,
                 scorer: L1Scorer, budget: DailyBudgetGuard,
                 budget_key: str = "l0l1") -> None:
        self.provider = provider
        self.store = store
        self.scorer = scorer
        self.budget = budget
        self.budget_key = budget_key

    def run(self, sources: list[DiscoverySource], *, enrich_details: bool = True,
            triggered_by: str = "system") -> RunSummary:
        run_id = self.store.create_run(
            "l0l1_discovery",
            "v1.0.0",
            triggered_by,
            platform=self.provider.platform_name,
        )
        observation_count = 0
        unique_platform_ids: dict[str, VideoObservation] = {}
        try:
            for source in sources:
                self.budget.acquire(
                    provider=self.provider.provider_name,
                    budget_key=self.budget_key,
                    requests=1,
                )
                page = self._fetch(source)
                if page.cached:
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
                self.store.ingest(
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
                observation_count += len(items)
                for item in items:
                    key = f"{item.video.platform}:{item.video.platform_video_id}"
                    unique_platform_ids[key] = item

            if enrich_details and unique_platform_ids:
                ids = [item.video.platform_video_id for item in unique_platform_ids.values()]
                self.budget.acquire(
                    provider=self.provider.provider_name,
                    budget_key=self.budget_key,
                    requests=math.ceil(len(ids) / 50),
                )
                details = self.provider.fetch_videos(ids)
                self._validate_platform(details)
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
            self.store.finish_run(
                run_id,
                input_count=observation_count,
                output_count=len(unique_platform_ids),
                promoted_l1_count=len(scores),
                summary={"source_count": len(sources), "llm_calls": 0,
                         "platform": self.provider.platform_name,
                         "enrich_details": enrich_details},
            )
            return RunSummary(
                run_id=run_id,
                platform=self.provider.platform_name,
                source_count=len(sources),
                observations=observation_count,
                unique_platform_videos=len(unique_platform_ids),
                scores=scores,
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
