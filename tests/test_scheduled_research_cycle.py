import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "windmill/f/content_research/collectors/select_cycle_media.py"
spec = importlib.util.spec_from_file_location("select_cycle_media", SCRIPT)
selector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(selector)
DSN = os.environ.get("TEST_DATABASE_URL")


def test_flow_binds_persisted_batches_and_runs_media_serially_without_cloud_submission():
    # Structural source contract; CLI parsing and actual execution are separate acceptance checks.
    flow = (ROOT / "windmill/f/content_research/flows/scheduled_research_cycle.flow/flow.yaml").read_text()
    for fragment in (
        "concurrent_limit: 1", "properties: {}", "additionalProperties: false",
        "expr: result.status === 'deferred' || result.new_candidate_count === 0 || result.scored_videos === 0", "skip_if_stopped: true",
        "source_run_id:\n            type: javascript\n            expr: results.discovery.run_id",
        "comment_run_id:\n            type: javascript\n            expr: results.comments.run_id",
        "expr: results.media_selection.video_ids", "expr: flow_input.iter.value",
        "parallel: false", "skip_failures: false",
    ):
        assert fragment in flow
    assert "retry:" not in flow and "run_reviewed" not in flow
    schedule = (ROOT / "windmill/f/content_research/flows/scheduled_research_cycle.schedule.yaml").read_text()
    for fragment in ("enabled: false", "is_flow: true", "args: {}", "timezone: Etc/UTC"):
        assert fragment in schedule
    assert 'schedule: "0 10 */6 * * *"' in schedule
    assert 'schedule: "0 10 * * * *"' not in schedule


@pytest.fixture
def media_batch():
    if not DSN:
        pytest.skip("TEST_DATABASE_URL not configured")
    run_id = uuid4()
    videos = [uuid4() for _ in range(6)]
    with psycopg.connect(DSN) as conn:
        conn.execute("""insert into pipeline_run(id,run_type,run_version,platform,status)
            values (%s,'comment_l2_l3_batch','cycle-test','douyin','success')""", (run_id,))
        for video in videos:
            conn.execute("insert into source_video(id,platform,platform_video_id) values (%s,'douyin',%s)",
                         (video, str(video)))
    try:
        yield run_id, videos
    finally:
        with psycopg.connect(DSN) as conn:
            conn.execute("delete from pipeline_run_item where run_id=%s", (run_id,))
            conn.execute("delete from pipeline_run where id=%s", (run_id,))
            conn.execute("delete from source_video where id=any(%s)", (videos,))


def add_item(run_id, video, stage="L2", outcome="success"):
    with psycopg.connect(DSN) as conn:
        conn.execute("""insert into pipeline_run_item(run_id,entity_type,entity_id,stage,outcome)
            values (%s,'video',%s,%s,%s)""", (run_id, video, stage, outcome))


def test_media_selection_is_batch_bound_and_only_successful_l2(media_batch):
    run_id, videos = media_batch
    add_item(run_id, videos[0])
    add_item(run_id, videos[1], outcome="failed")
    add_item(run_id, videos[2], stage="COMMENTS", outcome="collected")
    first = selector.select(DSN, run_id)
    assert first == [str(videos[0])]
    assert selector.select(DSN, run_id) == first


@pytest.mark.parametrize("status", ["running", "failed", "partial"])
def test_media_selection_refuses_non_successful_parent(media_batch, status):
    run_id, videos = media_batch
    add_item(run_id, videos[0])
    with psycopg.connect(DSN) as conn:
        conn.execute("update pipeline_run set status=%s where id=%s", (status, run_id))
    with pytest.raises(ValueError, match="successful comment"):
        selector.select(DSN, run_id)


def test_empty_batch_is_a_real_zero_selection(media_batch):
    assert selector.select(DSN, media_batch[0]) == []


def test_oversized_batch_is_rejected_not_silently_truncated(media_batch):
    run_id, videos = media_batch
    for video in videos:
        add_item(run_id, video)
    with pytest.raises(ValueError, match="exceeds"):
        selector.select(DSN, run_id)


def test_scheduled_discovery_shares_real_manual_lock_and_releases_it(media_batch):
    path = ROOT / "windmill/f/content_research/collectors/scheduled_golden_intake.py"
    module_spec = importlib.util.spec_from_file_location("scheduled_lock_check", path)
    worker = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(worker)
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("select pg_advisory_lock(hashtext(%s))", (worker.LOCK_NAME,))
        with worker._single_paid_job(DSN) as acquired:
            assert acquired is False
        conn.execute("select pg_advisory_unlock(hashtext(%s))", (worker.LOCK_NAME,))
        with worker._single_paid_job(DSN) as acquired:
            assert acquired is True
            assert conn.execute("select pg_try_advisory_lock(hashtext(%s))", (worker.LOCK_NAME,)).fetchone()[0] is False
        assert conn.execute("select pg_try_advisory_lock(hashtext(%s))", (worker.LOCK_NAME,)).fetchone()[0] is True
        conn.execute("select pg_advisory_unlock(hashtext(%s))", (worker.LOCK_NAME,))
