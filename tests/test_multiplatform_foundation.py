from __future__ import annotations

import os
from datetime import datetime, timezone

import psycopg
import pytest

from douyin_research.l0l1 import DiscoveryContext, L0L1Store, L1Scorer
from douyin_research.providers import TikHubDouyinProvider, TikHubProvider
from douyin_research.providers.types import (
    AccountRef,
    MetricSnapshotInput,
    VideoObservation,
    VideoRef,
)

DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not configured")


def observation(platform: str, external_id: str) -> VideoObservation:
    now = datetime(2026, 9, 19, 4, 0, tzinfo=timezone.utc)
    return VideoObservation(
        video=VideoRef(
            provider="fake",
            platform=platform,
            platform_video_id=external_id,
            account_platform_id="same-account-id",
            title=f"{platform}:{external_id}",
            observed_at=now,
        ),
        account=AccountRef(
            provider="fake",
            platform=platform,
            platform_account_id="same-account-id",
            nickname=platform,
            follower_count=1000,
            observed_at=now,
        ),
        metrics=MetricSnapshotInput(
            platform=platform,
            video_platform_id=external_id,
            captured_at=now,
            provider="fake",
            source_endpoint="fixture",
            like_count=100,
            author_follower_count=1000,
        ),
    )


def clear_business_tables() -> None:
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


def test_platform_registry_seeded_and_only_douyin_enabled() -> None:
    assert DSN
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select platform_key, enabled, provider_status
            from platform_registry
            order by sort_order
            """
        )
        rows = cur.fetchall()

    assert [r[0] for r in rows] == [
        "douyin",
        "kuaishou",
        "wechat_channels",
        "xiaohongshu",
        "bilibili",
        "weibo",
    ]
    assert rows[0][1:] == (True, "active")
    assert all(r[1] is False and r[2] == "planned" for r in rows[1:])


def test_same_external_id_is_distinct_across_platforms() -> None:
    assert DSN
    clear_business_tables()
    store = L0L1Store(DSN)
    run_id = store.create_run("multiplatform_fixture", "v1", platform=None)
    context = DiscoveryContext(
        run_id=run_id,
        source_type="fixture",
        source_key="same-id",
        provider="fake",
        request_fingerprint="fp",
        source_count=2,
        ranks={"123": 1},
    )

    store.ingest(
        [observation("douyin", "123"), observation("kuaishou", "123")],
        context,
    )

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select platform, platform_video_id
            from source_video
            where platform_video_id='123'
            order by platform
            """
        )
        videos = cur.fetchall()
        cur.execute(
            """
            select platform, platform_account_id
            from source_account
            where platform_account_id='same-account-id'
            order by platform
            """
        )
        accounts = cur.fetchall()
        cur.execute("select count(*) from discovery_event")
        discovery_count = cur.fetchone()[0]
        cur.execute("select count(*) from metric_snapshot")
        metric_count = cur.fetchone()[0]

    assert videos == [("douyin", "123"), ("kuaishou", "123")]
    assert accounts == [("douyin", "same-account-id"), ("kuaishou", "same-account-id")]
    assert discovery_count == 2
    assert metric_count == 2


def test_cross_platform_l1_scoring_is_rejected() -> None:
    assert DSN
    clear_business_tables()
    store = L0L1Store(DSN)
    run_id = store.create_run("multiplatform_fixture", "v1", platform=None)
    context = DiscoveryContext(
        run_id=run_id,
        source_type="fixture",
        source_key="mixed",
        provider="fake",
        request_fingerprint="fp-mixed",
        source_count=2,
        ranks={"1": 1, "2": 2},
    )
    store.ingest(
        [observation("douyin", "1"), observation("kuaishou", "2")],
        context,
    )

    with pytest.raises(ValueError, match="cross-platform L1 scoring"):
        L1Scorer(DSN).score_run(run_id)


def test_tikhub_alias_is_explicit_douyin_adapter() -> None:
    assert TikHubProvider is TikHubDouyinProvider
    assert TikHubDouyinProvider.platform_name == "douyin"
    assert TikHubDouyinProvider.video_batch_size == 50
