from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from douyin_research.l0l1 import DiscoveryContext, L0L1Store, L1Scorer
from douyin_research.providers.types import (
    AccountRef,
    MetricSnapshotInput,
    VideoObservation,
    VideoRef,
)


DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not configured")


def obs(
    vid: str,
    uid: str,
    likes: int,
    followers: int,
    when: datetime,
) -> VideoObservation:
    account = AccountRef(
        provider="tikhub",
        platform="douyin",
        platform_account_id=uid,
        sec_user_id=uid,
        nickname=uid,
        follower_count=followers,
        observed_at=when,
    )
    video = VideoRef(
        provider="tikhub",
        platform="douyin",
        platform_video_id=vid,
        account_platform_id=uid,
        title=vid,
        description=vid,
        published_at=when - timedelta(hours=6),
        duration_ms=30000,
        observed_at=when,
    )
    metrics = MetricSnapshotInput(
        platform="douyin",
        video_platform_id=vid,
        captured_at=when,
        provider="tikhub",
        source_endpoint="fixture",
        like_count=likes,
        comment_count=likes // 10,
        share_count=likes // 20,
        author_follower_count=followers,
    )
    return VideoObservation(video=video, account=account, metrics=metrics)


def clear_db() -> None:
    assert DSN
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        for table in (
            "video_score",
            "pipeline_run_item",
            "pipeline_run",
            "metric_snapshot",
            "discovery_event",
            "account_metric_snapshot",
            "provider_entity_lineage",
            "source_video",
            "source_account",
        ):
            cur.execute(f"delete from {table}")
        conn.commit()


def test_l0_ingest_is_idempotent_and_keeps_multi_source_evidence() -> None:
    assert DSN
    clear_db()
    store = L0L1Store(DSN)
    run_id = store.create_run("l0_discovery", "v1")
    when = datetime(2026, 9, 19, 3, 0, tzinfo=timezone.utc)

    batch = [obs("v1", "u1", 1000, 500, when), obs("v2", "u2", 100, 5000, when)]
    c1 = DiscoveryContext(
        run_id=run_id,
        source_type="low_fan",
        source_key="low_fan_24h",
        provider="tikhub",
        request_fingerprint="fp1",
        source_count=2,
        ranks={"v1": 1, "v2": 2},
    )
    first = store.ingest(batch, c1)
    again = store.ingest(batch, c1)

    c2 = DiscoveryContext(
        run_id=run_id,
        source_type="search",
        source_key="search_myth",
        provider="tikhub",
        request_fingerprint="fp2",
        source_count=1,
        ranks={"v1": 1},
    )
    store.ingest([batch[0]], c2)

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("select count(*) from source_video")
        assert cur.fetchone()[0] == 2
        cur.execute("select count(*) from discovery_event")
        assert cur.fetchone()[0] == 3
        cur.execute("select count(*) from metric_snapshot")
        # Same run + same video + same source endpoint is one metric observation.
        assert cur.fetchone()[0] == 2

    assert first.new_videos == 2
    assert again.new_videos == 0
    assert again.discovery_inserted == 0
    assert again.metric_inserted == 0


def test_l1_scoring_is_relative_and_schedules_next_due() -> None:
    assert DSN
    clear_db()
    store = L0L1Store(DSN)
    run_id = store.create_run("l0_discovery", "v1")
    when = datetime(2026, 9, 19, 3, 0, tzinfo=timezone.utc)

    batch = [
        obs("strong", "u1", 5000, 500, when),
        obs("mid", "u2", 500, 2000, when),
        obs("weak", "u3", 50, 10000, when),
    ]
    ctx = DiscoveryContext(
        run_id=run_id,
        source_type="low_fan",
        source_key="low_fan_24h",
        provider="tikhub",
        request_fingerprint="fp-score",
        source_count=3,
        ranks={"strong": 1, "mid": 2, "weak": 3},
    )
    store.ingest(batch, ctx)
    scores = L1Scorer(DSN).score_run(run_id, now=when)

    assert len(scores) == 3

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select platform_video_id, monitoring_priority, research_level, next_due_at
            from source_video
            order by monitoring_priority desc
            """
        )
        rows = cur.fetchall()

    assert rows[0][0] == "strong"
    assert rows[-1][0] == "weak"
    assert all(row[2] == 1 for row in rows)
    assert all(row[3] is not None for row in rows)


def test_l1_does_not_create_analysis_runs() -> None:
    assert DSN
    clear_db()
    store = L0L1Store(DSN)
    run_id = store.create_run("l0_discovery", "v1")
    when = datetime(2026, 9, 19, 3, 0, tzinfo=timezone.utc)
    store.ingest(
        [obs("v1", "u1", 100, 1000, when)],
        DiscoveryContext(
            run_id=run_id,
            source_type="search",
            source_key="q",
            provider="tikhub",
            request_fingerprint="fp",
            source_count=1,
            ranks={"v1": 1},
        ),
    )
    L1Scorer(DSN).score_run(run_id, now=when)

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("select count(*) from analysis_run")
        assert cur.fetchone()[0] == 0
