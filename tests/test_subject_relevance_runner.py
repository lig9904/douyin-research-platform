from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from douyin_research.l0l1.ingest import IngestResult
from douyin_research.l0l1.runner import DiscoverySource, L0L1Runner
from douyin_research.providers.types import ProviderPage, VideoObservation, VideoRef


def _observation(identifier: str) -> VideoObservation:
    return VideoObservation(
        video=VideoRef(
            provider="fake", platform="douyin", platform_video_id=identifier,
            title=identifier, observed_at=datetime(2026, 9, 24, tzinfo=timezone.utc),
        ),
        account=None, metrics=None,
    )


class _Provider:
    provider_name = "fake"
    platform_name = "douyin"
    video_batch_size = 50

    def __init__(self, *, fail_detail: bool = False) -> None:
        self.fail_detail = fail_detail
        self.detail_batches: list[list[str]] = []

    def discover(self, _kind: str, **_kwargs):
        return ProviderPage(
            items=[_observation("relevant"), _observation("pending"), _observation("irrelevant")],
            endpoint_key="fake.discovery", request_fingerprint="discovery", cached=False,
        )

    def fetch_videos(self, identifiers):
        batch = list(identifiers)
        self.detail_batches.append(batch)
        if self.fail_detail:
            raise RuntimeError("supplier detail failure")
        return [_observation(identifier) for identifier in batch]


class _Store:
    def __init__(self) -> None:
        self.run_id = uuid4()
        self.ids = {name: uuid4() for name in ("relevant", "pending", "irrelevant")}
        self.finished: list[dict] = []
        self.flags: set[object] | None = None

    def create_run(self, *_args, **_kwargs):
        return self.run_id

    def ingest(self, items, context):
        identifiers = [item.video.platform_video_id for item in items]
        return IngestResult(
            video_ids=[self.ids[item] for item in identifiers],
            new_video_ids=[self.ids[item] for item in identifiers] if context.record_discovery else [],
            new_platform_video_ids=identifiers if context.record_discovery else [],
            new_videos=len(identifiers) if context.record_discovery else 0,
            discovery_inserted=len(identifiers) if context.record_discovery else 0,
            metric_inserted=len(identifiers),
            new_project_video_ids=[self.ids[item] for item in identifiers] if context.record_discovery else [],
        )

    def platform_video_ids(self, video_ids):
        inverse = {value: key for key, value in self.ids.items()}
        return {inverse[identifier] for identifier in video_ids}

    def set_new_candidate_flags(self, _run_id, identifiers):
        self.flags = set(identifiers)

    def finish_run(self, _run_id, **kwargs):
        self.finished.append(kwargs)


class _Relevance:
    def __init__(self, store: _Store) -> None:
        self.store = store
        self.calls: list[tuple[str, set[object]]] = []

    def evaluate_run(self, *, video_ids, stage: str, **_kwargs):
        ids = set(video_ids)
        self.calls.append((stage, ids))
        if stage == "discovery":
            return {
                self.store.ids["relevant"]: "relevant",
                self.store.ids["pending"]: "pending",
                self.store.ids["irrelevant"]: "irrelevant",
            }
        assert ids == {self.store.ids["relevant"]}
        return {self.store.ids["relevant"]: "relevant"}


class _Scorer:
    def score_run(self, *_args, **_kwargs):
        raise AssertionError("project path must not write shared L1 scores")


class _Budget:
    def acquire(self, **_kwargs):
        pass

    def refund(self, **_kwargs):
        pass


def _runner(*, fail_detail: bool = False):
    store = _Store()
    provider = _Provider(fail_detail=fail_detail)
    relevance = _Relevance(store)
    runner = L0L1Runner(
        provider=provider, store=store, scorer=_Scorer(), budget=_Budget(), relevance=relevance,
    )
    return runner, store, provider, relevance


def test_subject_gate_runs_before_detail_and_preserves_pending_for_review() -> None:
    runner, store, provider, relevance = _runner()

    summary = runner.run(
        [DiscoverySource("low_fan", "golden_low_fan", "page")],
        project_id=uuid4(), subject_id=uuid4(), enrich_new_only=False,
    )

    assert provider.detail_batches == [["relevant"]]
    assert relevance.calls == [
        ("discovery", set(store.ids.values())),
        ("detail_enrichment", {store.ids["relevant"]}),
    ]
    assert store.flags == {store.ids["relevant"]}
    assert summary.relevant_candidate_count == 1
    assert summary.pending_candidate_count == 1
    assert summary.irrelevant_candidate_count == 1
    assert store.finished[-1]["summary"]["subject_scoring_status"] == "deferred_project_score_storage"


def test_detail_failure_keeps_pre_gate_audit_and_does_not_claim_subject_scoring() -> None:
    runner, store, provider, relevance = _runner(fail_detail=True)

    with pytest.raises(RuntimeError, match="supplier detail failure"):
        runner.run(
            [DiscoverySource("low_fan", "golden_low_fan", "page")],
            project_id=uuid4(), subject_id=uuid4(), enrich_new_only=False,
        )

    assert provider.detail_batches == [["relevant"]]
    assert relevance.calls == [("discovery", set(store.ids.values()))]
    assert store.finished[-1]["status"] == "failed"
    assert store.finished[-1]["summary"]["stage"] == "detail_enrichment"
