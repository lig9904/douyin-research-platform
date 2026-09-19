from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import psycopg
import pytest

from douyin_research.l0l1.budget import DailyBudgetGuard
from douyin_research.l0l1.ingest import L0L1Store
from douyin_research.l0l1.runner import DiscoverySource, L0L1Runner
from douyin_research.l0l1.scoring import L1Scorer
from douyin_research.providers.errors import ProviderBudgetError
from douyin_research.providers.types import (
    AccountRef, MetricSnapshotInput, ProviderPage, VideoObservation, VideoRef
)

DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not configured")


def observation(vid: str, likes: int, followers: int) -> VideoObservation:
    now = datetime(2026, 9, 19, 3, 0, tzinfo=timezone.utc)
    return VideoObservation(
        video=VideoRef(
            provider="fake", platform="douyin", platform_video_id=vid,
            account_platform_id=f"u-{vid}", title=vid, published_at=now, observed_at=now
        ),
        account=AccountRef(
            provider="fake", platform="douyin", platform_account_id=f"u-{vid}",
            sec_user_id=f"u-{vid}", nickname=f"u-{vid}",
            follower_count=followers, observed_at=now
        ),
        metrics=MetricSnapshotInput(
            platform="douyin",
            video_platform_id=vid, captured_at=now, provider="fake",
            source_endpoint="fake.discovery", like_count=likes,
            comment_count=likes // 10, share_count=likes // 20,
            author_follower_count=followers
        ),
    )


class FakeProvider:
    provider_name = "fake"
    platform_name = "douyin"
    capabilities = frozenset({
        "discover.low_fan",
        "search.videos",
        "video.batch_detail",
    })

    def __init__(self) -> None:
        self.detail_batches: list[list[str]] = []

    def discover(self, kind: str, **kwargs: Any):
        if kind == "low_fan":
            return self.fetch_low_fan_billboard(**kwargs)
        if kind in {"creator", "creator_material"}:
            return self.fetch_creator_material(**kwargs)
        raise ValueError(kind)

    def fetch_low_fan_billboard(self, **kwargs: Any):
        return ProviderPage(
            items=[observation("a", 1000, 500), observation("b", 100, 5000)],
            endpoint_key="fake.low_fan", request_fingerprint="low-fp", cached=False
        )

    def search_videos(self, query: str, **kwargs: Any):
        return ProviderPage(
            items=[observation("a", 1000, 500), observation("c", 300, 3000)],
            endpoint_key="fake.search", request_fingerprint="search-fp", cached=False
        )

    def fetch_creator_material(self, **kwargs: Any):
        return ProviderPage(items=[], endpoint_key="fake.creator",
                            request_fingerprint="creator-fp", cached=False)

    def fetch_videos(self, video_ids, **kwargs: Any):
        ids = list(video_ids)
        out = []
        for i in range(0, len(ids), 50):
            batch = ids[i:i+50]
            self.detail_batches.append(batch)
            out.extend(
                observation(x, 2000 if x == "a" else 400, 500 if x == "a" else 3000)
                for x in batch
            )
        return out


def clear() -> None:
    assert DSN
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        for table in (
            "video_score","pipeline_run_item","pipeline_run","metric_snapshot",
            "discovery_event","account_metric_snapshot","provider_entity_lineage",
            "source_video","source_account","daily_budget"
        ):
            cur.execute(f"delete from {table}")
        conn.commit()


def test_two_sources_overlap_to_one_entity_and_zero_llm_calls() -> None:
    assert DSN
    clear()
    budget = DailyBudgetGuard(DSN)
    budget.configure(provider="fake", budget_key="l0l1", max_requests=10, max_cost=None)

    provider = FakeProvider()
    runner = L0L1Runner(
        provider=provider, store=L0L1Store(DSN),
        scorer=L1Scorer(DSN), budget=budget
    )
    summary = runner.run(
        [
            DiscoverySource("low_fan", "low_fan", "24h", max_items=10),
            DiscoverySource("search", "search", "myth", {"query": "神话"}, max_items=10),
        ],
        enrich_details=True,
    )

    assert summary.unique_platform_videos == 3
    assert len(summary.scores) == 3
    assert [len(x) for x in provider.detail_batches] == [3]

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("select count(*) from source_video")
        assert cur.fetchone()[0] == 3
        cur.execute("select count(*) from discovery_event")
        assert cur.fetchone()[0] == 4
        cur.execute("select summary->>'llm_calls' from pipeline_run where id=%s", (summary.run_id,))
        assert cur.fetchone()[0] == "0"
        cur.execute("select count(*) from analysis_run")
        assert cur.fetchone()[0] == 0


def test_budget_stops_before_second_external_call() -> None:
    assert DSN
    clear()
    guard = DailyBudgetGuard(DSN)
    guard.configure(provider="fake", budget_key="tiny", max_requests=1, max_cost=None)
    guard.acquire(provider="fake", budget_key="tiny", requests=1)
    with pytest.raises(ProviderBudgetError):
        guard.acquire(provider="fake", budget_key="tiny", requests=1)

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "select used_requests from daily_budget where provider='fake' and budget_key='tiny'"
        )
        assert cur.fetchone()[0] == 1
