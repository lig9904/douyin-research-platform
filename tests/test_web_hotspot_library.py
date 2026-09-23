from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path
from urllib.parse import urlparse

import psycopg
import pytest


DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not configured")


@pytest.fixture(autouse=True)
def legacy_admin_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WM_END_USER_EMAIL", "fixture@example.com")
    wmill = types.ModuleType("wmill")
    wmill.get_variable = lambda _path: "fixture@example.com"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "wmill", wmill)


def load_backend():
    path = Path(
        "windmill/f/content_research/research_dashboard.raw_app/"
        "backend/get_hotspot_library.py"
    )
    spec = importlib.util.spec_from_file_location("get_hotspot_library", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def resource_from_dsn(dsn: str) -> dict:
    u = urlparse(dsn)
    return {
        "host": u.hostname or "127.0.0.1",
        "port": u.port or 5432,
        "user": u.username or "",
        "password": u.password or "",
        "dbname": u.path.lstrip("/"),
        "sslmode": "disable",
    }


def clear_and_seed() -> str:
    assert DSN
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        for table in (
            "collection_item",
            "collection",
            "signal_video_link",
            "signal_snapshot",
            "external_signal",
            "video_score",
            "metric_snapshot",
            "source_video",
            "source_account",
        ):
            cur.execute(f"delete from {table}")

        cur.execute(
            """
            insert into source_account(platform, platform_account_id, nickname)
            values ('douyin','hot-acct-1','渔海老张')
            returning id
            """
        )
        account_id = cur.fetchone()[0]

        cur.execute(
            """
            insert into source_video(
              platform, platform_video_id, account_id, title, source_url,
              published_at, duration_ms, research_level,
              monitoring_status, monitoring_priority
            )
            values (
              'douyin','hot-video-1',%s,
              '秦皇岛3天2晚旅游攻略！必打卡',
              'https://example.com/hot-video-1',
              now()-interval '1 day',158000,1,'observe',86
            )
            returning id
            """,
            (account_id,),
        )
        video_id = cur.fetchone()[0]

        cur.execute(
            """
            insert into metric_snapshot(
              video_id, provider, source_endpoint, observation_key, captured_at,
              play_count, like_count, comment_count, share_count,
              author_follower_count
            )
            values (
              %s,'tikhub','fixture','hot-video-metric',now(),
              5286000,321000,28000,46000,123500
            )
            """,
            (video_id,),
        )

        cur.execute(
            """
            insert into video_score(video_id, score_type, score, rule_version, components)
            values (%s,'priority',86,'test','{}'::jsonb)
            """,
            (video_id,),
        )

        cur.execute(
            """
            insert into external_signal(
              provider, platform, signal_type, provider_signal_id,
              signal_key, title, description, category_key, city_code,
              research_level, monitoring_status, monitoring_priority,
              first_seen_at, last_seen_at
            )
            values (
              'tikhub','douyin','hot_topic','provider-hot-1',
              '秦皇岛旅游','#秦皇岛旅游',
              '秦皇岛滨海旅游相关热门话题',
              '旅游','qinhuangdao',
              1,'monitoring',90,
              now()-interval '2 days',now()
            )
            returning id
            """
        )
        signal_id = cur.fetchone()[0]

        cur.execute(
            """
            insert into signal_snapshot(
              signal_id, captured_at, rank_value, rank_change,
              heat_value, value_json
            )
            values
              (
                %s,now()-interval '1 day',3,2,
                1000000,'{"discussion_count":"200000"}'::jsonb
              ),
              (
                %s,now(),1,2,
                2000000,'{"discussion_count":"320000"}'::jsonb
              )
            """,
            (signal_id, signal_id),
        )

        cur.execute(
            """
            insert into signal_video_link(
              signal_id, video_id, relation_type, observed_at, metadata
            )
            values (%s,%s,'related',now(),'{}'::jsonb)
            """,
            (signal_id, video_id),
        )

        cur.execute(
            """
            insert into collection(name, description, created_by)
            values ('热点专题','测试热点专题','tester')
            returning id
            """
        )
        collection_id = cur.fetchone()[0]

        cur.execute(
            """
            insert into collection_item(collection_id, signal_id, note)
            values (%s,%s,'测试热点收藏')
            """,
            (collection_id, signal_id),
        )

        conn.commit()

    return str(signal_id)


def test_hotspot_library_filters_growth_trend_and_related_videos() -> None:
    assert DSN
    signal_id = clear_and_seed()
    module = load_backend()

    result = module.main(
        resource_from_dsn(DSN),
        platform="douyin",
        days=7,
        signal_type="hot_topic",
        category="旅游",
        research_level=1,
        monitoring_status="monitoring",
        heat_min=1500000,
        query="秦皇岛",
        page=1,
        page_size=10,
        sort="growth_desc",
        selected_signal_id=signal_id,
    )

    assert result["total"] == 1
    assert len(result["items"]) == 1

    item = result["items"][0]
    assert item["title"] == "#秦皇岛旅游"
    assert item["heat_value"] == 2000000
    assert item["discussion_count"] == 320000
    assert item["related_video_count"] == 1
    assert item["collection_count"] == 1
    assert float(item["heat_growth_pct"]) == pytest.approx(100.0)

    detail = result["detail"]
    assert detail["id"] == signal_id
    assert len(detail["trend"]) == 2
    assert len(detail["related_videos"]) == 1
    assert detail["related_videos"][0]["title"] == "秦皇岛3天2晚旅游攻略！必打卡"


def test_hotspot_growth_is_null_with_one_snapshot() -> None:
    assert DSN
    signal_id = clear_and_seed()
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            delete from signal_snapshot
            where signal_id=%s::uuid
              and captured_at < (select max(captured_at) from signal_snapshot where signal_id=%s::uuid)
            """,
            (signal_id, signal_id),
        )
        conn.commit()

    module = load_backend()
    result = module.main(
        resource_from_dsn(DSN),
        platform="douyin",
        days=7,
        selected_signal_id=signal_id,
    )

    assert result["items"][0]["heat_growth_pct"] is None
    assert len(result["detail"]["trend"]) == 1


def test_hotspot_library_empty_search() -> None:
    assert DSN
    clear_and_seed()
    module = load_backend()

    result = module.main(
        resource_from_dsn(DSN),
        platform="douyin",
        days=7,
        query="不存在的热点",
    )

    assert result["total"] == 0
    assert result["items"] == []
    assert result["detail"] == {}
