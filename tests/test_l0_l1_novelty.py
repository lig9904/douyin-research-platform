from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from douyin_research.l0l1.ingest import IngestResult
from douyin_research.l0l1.runner import DiscoverySource, L0L1Runner
from douyin_research.providers.types import ProviderPage, VideoObservation, VideoRef


def _observation(platform_video_id: str) -> VideoObservation:
    return VideoObservation(
        video=VideoRef(
            provider="fake",
            platform="douyin",
            platform_video_id=platform_video_id,
            title=platform_video_id,
            observed_at=datetime(2026, 9, 21, tzinfo=timezone.utc),
        ),
        account=None,
        metrics=None,
    )


class _Provider:
    provider_name = "fake"
    platform_name = "douyin"
    video_batch_size = 50

    def __init__(self) -> None:
        self.detail_batches: list[list[str]] = []

    def discover(self, _kind: str, **_kwargs):
        return ProviderPage(
            items=[_observation("old"), _observation("new")],
            endpoint_key="fake.discovery",
            request_fingerprint="discovery-fingerprint",
            cached=False,
        )

    def fetch_videos(self, video_ids):
        identifiers = list(video_ids)
        self.detail_batches.append(identifiers)
        return [_observation(identifier) for identifier in identifiers]


class _Store:
    def __init__(self, *, new_platform_ids: set[str]) -> None:
        self.run_id = uuid4()
        self.ids = {"old": uuid4(), "new": uuid4()}
        self.new_platform_ids = new_platform_ids
        self.flags: tuple[object, set[object]] | None = None
        self.finished: dict | None = None

    def create_run(self, *_args, **_kwargs):
        return self.run_id

    def ingest(self, items, context):
        platform_ids = [item.video.platform_video_id for item in items]
        new_platform_ids = (
            [item for item in platform_ids if item in self.new_platform_ids]
            if context.record_discovery
            else []
        )
        return IngestResult(
            video_ids=[self.ids[item] for item in platform_ids],
            new_video_ids=[self.ids[item] for item in new_platform_ids],
            new_platform_video_ids=new_platform_ids,
            new_videos=len(new_platform_ids),
            discovery_inserted=len(platform_ids) if context.record_discovery else 0,
            metric_inserted=len(platform_ids),
        )

    def set_new_candidate_flags(self, run_id, identifiers):
        self.flags = (run_id, set(identifiers))

    def finish_run(self, _run_id, **kwargs):
        self.finished = kwargs


class _Scorer:
    def __init__(self, store: _Store) -> None:
        self.store = store

    def score_run(self, _run_id):
        return {identifier: 50.0 for identifier in self.store.ids.values()}


class _Budget:
    def acquire(self, **_kwargs):
        raise AssertionError("provider-reserved mode must not use legacy budget calls")

    def refund(self, **_kwargs):
        raise AssertionError("provider-reserved mode must not use legacy budget calls")


def _run(new_platform_ids: set[str], *, enrich_new_only: bool = True):
    store = _Store(new_platform_ids=new_platform_ids)
    provider = _Provider()
    summary = L0L1Runner(
        provider=provider,
        store=store,
        scorer=_Scorer(store),
        budget=_Budget(),
        provider_reserves_budget=True,
    ).run(
        [DiscoverySource("low_fan", "golden_low_fan", "72h-page-1")],
        enrich_new_only=enrich_new_only,
    )
    return summary, store, provider


def test_mixed_discovery_enriches_and_flags_only_new_candidates() -> None:
    summary, store, provider = _run({"new"})

    assert summary.unique_platform_videos == 2
    assert summary.new_candidate_count == 1
    assert provider.detail_batches == [["new"]]
    assert store.flags == (store.run_id, {store.ids["new"]})
    assert store.finished["summary"]["new_candidate_count"] == 1
    assert store.finished["summary"]["detail_enriched_count"] == 1


def test_all_existing_discovery_skips_paid_detail_enrichment() -> None:
    summary, store, provider = _run(set())

    assert summary.unique_platform_videos == 2
    assert summary.new_candidate_count == 0
    assert provider.detail_batches == []
    assert store.flags == (store.run_id, set())
    assert store.finished["summary"]["detail_enriched_count"] == 0


def test_manual_default_still_enriches_existing_videos() -> None:
    summary, store, provider = _run(set(), enrich_new_only=False)

    assert summary.new_candidate_count == 0
    assert provider.detail_batches == [["old", "new"]]
    assert store.flags == (store.run_id, set())
    assert store.finished["summary"]["enrich_new_only"] is False
    assert store.finished["summary"]["detail_enriched_count"] == 2
