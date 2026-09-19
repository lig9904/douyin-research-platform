from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from urllib.parse import urlparse

import psycopg
import pytest


DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not configured")


def load_backend():
    path = Path(
        "windmill/f/content_research/research_dashboard.raw_app/"
        "backend/get_video_library.py"
    )
    spec = importlib.util.spec_from_file_location("get_video_library", path)
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
            "video_comment",
            "video_score",
            "collection_item",
            "collection",
            "pipeline_run_item",
            "metric_snapshot",
            "discovery_event",
            "account_metric_snapshot",
            "provider_entity_lineage",
            "external_signal",
            "external_api_call",
            "external_api_response",
            "pipeline_run",
            "source_video",
            "source_account",
            "daily_budget",
        ):
            cur.execute(f"delete from {table}")

        cur.execute(
            """
            insert into source_account(platform, platform_account_id, nickname)
            values ('douyin','acct-video-lib','渔海老张')
            returning id
            """
        )
        account_id = cur.fetchone()[0]

        cur.execute(
            """
            insert into source_video(
              platform, platform_video_id, account_id, title, description,
              source_url, published_at, duration_ms, research_level,
              monitoring_status, monitoring_priority, first_seen_at, last_seen_at
            )
            values (
              'douyin','video-lib-1',%s,
              '秦皇岛海边惊现龙王祭坛？',
              '当地渔民讲述百年传说',
              'https://example.com/video-lib-1',
              now()-interval '2 days',158000,1,'observe',88,
              now()-interval '2 days',now()-interval '1 hour'
            )
            returning id
            """,
            (account_id,),
        )
        video_id = cur.fetchone()[0]

        cur.execute(
            """
            insert into account_metric_snapshot(
              account_id, provider, source_endpoint, observation_key,
              follower_count, captured_at
            )
            values (%s,'tikhub','fixture','vl-account-metric',123000,now())
            """,
            (account_id,),
        )

        cur.execute(
            """
            insert into metric_snapshot(
              video_id, provider, source_endpoint, observation_key,
              play_count, like_count, comment_count, share_count,
              author_follower_count, captured_at
            )
            values (
              %s,'tikhub','fixture','vl-video-metric',
              5286000,321000,28000,46000,123000,now()
            )
            """,
            (video_id,),
        )

        cur.execute(
            """
            insert into video_score(video_id, score_type, score, rule_version, components)
            values (%s,'priority',88,'test','{}'::jsonb)
            """,
            (video_id,),
        )

        cur.execute(
            """
            insert into discovery_event(
              video_id, provider, source_type, source_key, observation_key,
              discovered_at, rank_value, rule_version, metadata
            )
            values
              (%s,'tikhub','low_fan','low_fan_24h','vl-discovery-1',
               now()-interval '3 hours',1,'test','{}'::jsonb),
              (%s,'tikhub','search','秦皇岛海边传说','vl-discovery-2',
               now()-interval '2 hours',2,'test','{}'::jsonb)
            """,
            (video_id, video_id),
        )

        cur.execute(
            """
            insert into video_comment(
              video_id, provider, platform_comment_id, text_content,
              like_count, captured_at
            )
            values
              (%s,'tikhub','comment-1','这也太神奇了！',23000,now()),
              (%s,'tikhub','comment-2','我爷爷也说过这个地方',8421,now())
            """,
            (video_id, video_id),
        )

        cur.execute(
            """
            insert into collection(name, description, created_by)
            values ('重点专题','测试专题','tester')
            returning id
            """
        )
        collection_id = cur.fetchone()[0]
        cur.execute(
            """
            insert into collection_item(collection_id, video_id, note)
            values (%s,%s,'测试收藏')
            """,
            (collection_id, video_id),
        )
        conn.commit()

    return str(video_id)


def test_video_library_filters_and_detail() -> None:
    assert DSN
    video_id = clear_and_seed()
    module = load_backend()

    result = module.main(
        resource_from_dsn(DSN),
        platform="douyin",
        days=30,
        research_level=1,
        source_type="low_fan",
        priority_min=80,
        status="observe",
        play_min=1000000,
        play_max=-1,
        follower_min=100000,
        follower_max=200000,
        collected="yes",
        query="龙王祭坛",
        page=1,
        page_size=10,
        sort="priority_desc",
        selected_video_id=video_id,
    )

    assert result["total"] == 1
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["title"] == "秦皇岛海边惊现龙王祭坛？"
    assert float(item["priority"]) == pytest.approx(88)
    assert set(item["sources"]) == {"low_fan", "search"}
    assert item["collection_count"] == 1

    detail = result["detail"]
    assert detail["id"] == video_id
    assert len(detail["evidence"]) == 2
    assert len(detail["comments"]) == 2
    assert detail["source_url"] == "https://example.com/video-lib-1"


def test_video_library_query_and_pagination_empty_state() -> None:
    assert DSN
    clear_and_seed()
    module = load_backend()

    result = module.main(
        resource_from_dsn(DSN),
        platform="douyin",
        days=30,
        query="不存在的关键词",
        page=1,
        page_size=10,
    )

    assert result["total"] == 0
    assert result["items"] == []
    assert result["detail"] == {}


def test_video_library_all_platform_mode() -> None:
    assert DSN
    clear_and_seed()
    module = load_backend()

    result = module.main(
        resource_from_dsn(DSN),
        platform="all",
        days=30,
        page=1,
        page_size=10,
    )

    assert result["total"] == 1
    assert result["items"][0]["platform"] == "douyin"
    keys = [x["key"] for x in result["platforms"]]
    assert "douyin" in keys and "kuaishou" in keys
