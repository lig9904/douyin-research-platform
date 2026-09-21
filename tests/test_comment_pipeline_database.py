"""Real persistence/promotion; synthetic collector and extractor, no paid calls."""
import os
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from douyin_research.l2.comment_pipeline import CommentPipeline, CommentPipelineSettings
from douyin_research.l2.promotion import L3PromotionGate

DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="isolated TEST_DATABASE_URL required")


@pytest.fixture
def batch():
    source, a, b = uuid4(), uuid4(), uuid4()
    quota = f"test-t3-{source}"
    with psycopg.connect(DSN) as conn:
        conn.execute("insert into pipeline_run(id,run_type,run_version,platform,status) values (%s,'l0l1_discovery','test','douyin','success')", (source,))
        for video in (a, b):
            conn.execute("insert into source_video(id,platform,platform_video_id,research_level) values (%s,'douyin',%s,2)", (video, str(video)))
            conn.execute("insert into pipeline_run_item(run_id,entity_type,entity_id,stage,outcome) values (%s,'video',%s,'L1','scored')", (source, video))
            conn.execute("insert into video_score(video_id,score_type,score,rule_version) values (%s,'priority',90,'test')", (video,))
    gate = L3PromotionGate(DSN)
    gate.configure_daily_quota(platform="douyin", max_items=10, quota_date=date(2026, 9, 21), quota_key=quota)
    yield source, a, b, quota, gate
    with psycopg.connect(DSN) as conn:
        # Delete only this fixture's records, never other test or user data.
        conn.execute("delete from research_promotion_batch where quota_key=%s", (quota,))
        conn.execute("delete from daily_research_quota where quota_key=%s", (quota,))
        conn.execute("delete from pipeline_run where summary->>'source_run_id'=%s or id=%s", (str(source), source))
        conn.execute("delete from source_video where id=any(%s)", ([a, b],))


class Collector:
    def __init__(self):
        self.calls = []
        self.fail = False

    def collect(self, video, **kwargs):
        self.calls.append(video)
        if self.fail:
            raise TimeoutError("unknown remote outcome")
        return SimpleNamespace(run_id=uuid4(), comments_returned=1, observations_inserted=1,
                               external_pages=1, estimated_api_cost_usd=0.001, cached=False)


class Extractor:
    def __init__(self, fail=None):
        self.fail = fail

    def extract(self, video):
        if video == self.fail:
            raise ValueError("synthetic extraction failure")
        return SimpleNamespace(snapshot_id=str(uuid4()), evidence_fingerprint=f"feature-{video}")


def test_scheduled_subset_collects_only_run_bound_new_candidates(batch):
    source, a, b, quota, gate = batch
    with psycopg.connect(DSN) as conn:
        conn.execute(
            "update pipeline_run_item set metadata=%s where run_id=%s and entity_id=%s",
            (Jsonb({"new_candidate": True}), source, a),
        )
        conn.execute(
            "update pipeline_run_item set metadata=%s where run_id=%s and entity_id=%s",
            (Jsonb({"new_candidate": False}), source, b),
        )
    collector = Collector()
    service = CommentPipeline(
        DSN,
        collector=collector,
        feature_extractor=Extractor(),
        promotion_gate=gate,
        worker_identity="scheduled-test-worker",
    )
    result = service.run(
        source,
        settings=CommentPipelineSettings(
            top_n=2,
            quota_date=date(2026, 9, 21),
            quota_key=quota,
            new_candidates_only=True,
        ),
    )
    assert collector.calls == [str(a)]
    assert [item.video_id for item in result.videos] == [a]


def test_recovery_creates_complete_immutable_eligibility_without_recollection(batch):
    source, a, b, quota, gate = batch
    collector, extractor = Collector(), Extractor(b)
    service = CommentPipeline(DSN, collector=collector, feature_extractor=extractor,
                              promotion_gate=gate, worker_identity="test-worker")
    settings = CommentPipelineSettings(top_n=2, quota_date=date(2026, 9, 21), quota_key=quota)
    first = service.run(source, settings=settings)
    assert first.promotion.selected_count == 1
    extractor.fail = None
    second = service.run(source, settings=settings)
    assert second.pipeline_run_id == first.pipeline_run_id
    assert second.eligibility_run_id != first.eligibility_run_id
    assert second.promotion.selected_count == 1
    assert len(collector.calls) == 2
    with psycopg.connect(DSN) as conn:
        old = conn.execute("select entity_id from pipeline_run_item where run_id=%s", (first.eligibility_run_id,)).fetchall()
        new = conn.execute("select entity_id from pipeline_run_item where run_id=%s", (second.eligibility_run_id,)).fetchall()
        assert {row[0] for row in old} == {a}
        assert {row[0] for row in new} == {a, b}
        assert conn.execute("select stage,outcome from pipeline_run_item where run_id=%s", (source,)).fetchall() == [("L1", "scored")] * 2
    service.run(source, settings=CommentPipelineSettings(top_n=3, min_score=80, quota_date=settings.quota_date, quota_key=quota))
    assert len(collector.calls) == 2


def test_unknown_collection_outcome_never_resubmits_or_promotes_old_l2(batch):
    source, a, b, quota, gate = batch
    collector = Collector()
    collector.fail = True
    service = CommentPipeline(DSN, collector=collector, feature_extractor=Extractor(),
                              promotion_gate=gate, worker_identity="test-worker")
    settings = CommentPipelineSettings(top_n=2, quota_date=date(2026, 9, 21), quota_key=quota)
    first = service.run(source, settings=settings)
    second = service.run(source, settings=settings)
    assert len(collector.calls) == 2
    assert first.eligibility_run_id is None and second.promotion is None
    assert all(item.outcome == "reconciliation_required" for item in second.videos)


def test_promotion_failure_is_visible_and_recovery_reuses_collection(batch):
    source, a, b, quota, gate = batch
    collector = Collector()
    class FailingGate:
        def promote(self, *args, **kwargs):
            raise RuntimeError("private-supplier-detail")
    service = CommentPipeline(DSN, collector=collector, feature_extractor=Extractor(),
                              promotion_gate=FailingGate(), worker_identity="test-worker")
    settings = CommentPipelineSettings(top_n=2, quota_date=date(2026, 9, 21), quota_key=quota)
    with pytest.raises(RuntimeError, match="inspect persisted run") as caught:
        service.run(source, settings=settings)
    assert "private-supplier" not in str(caught.value)
    with psycopg.connect(DSN) as conn:
        failed_id, status, error = conn.execute(
            "select id,status,error_summary from pipeline_run where run_type='comment_l2_l3_batch' and summary->>'source_run_id'=%s",
            (str(source),)).fetchone()
        assert status == "failed"
        assert error == {"error_code": "comment_batch_orchestration_failed"}
    service.promotion_gate = gate
    recovered = service.run(source, settings=settings)
    assert recovered.pipeline_run_id == failed_id
    assert recovered.promotion.selected_count == 2
    assert len(collector.calls) == 2
    with psycopg.connect(DSN) as conn:
        assert conn.execute("select status,error_summary from pipeline_run where id=%s", (failed_id,)).fetchone() == ("success", None)
