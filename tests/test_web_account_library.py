from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
from urllib.parse import urlparse

import psycopg
import pytest


DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not configured")


def load_backend():
    windmill_root = str(Path("windmill").resolve())
    if windmill_root not in sys.path:
        sys.path.insert(0, windmill_root)
    path = Path(
        "windmill/f/content_research/research_dashboard.raw_app/"
        "backend/get_account_library.py"
    )
    spec = importlib.util.spec_from_file_location("get_account_library", path)
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
            "video_score",
            "metric_snapshot",
            "discovery_event",
            "account_tag",
            "account_metric_snapshot",
            "provider_entity_lineage",
            "source_video",
            "source_account",
        ):
            cur.execute(f"delete from {table}")

        cur.execute(
            """
            insert into source_account(
              platform, platform_account_id, nickname, profile_url, bio,
              location_text, account_type, certification_type,
              research_level, monitoring_status, monitoring_priority
            )
            values (
              'douyin','yuhai_laozhang','渔海老张',
              'https://example.com/yuhai_laozhang',
              '用镜头记录大海和生活',
              '河北·秦皇岛','个人','个人认证',
              1,'monitoring',85
            )
            returning id
            """
        )
        account_id = cur.fetchone()[0]

        cur.execute(
            """
            insert into account_tag(account_id, tag_type, tag_value, source)
            values
              (%s,'content_domain','旅行风景','manual'),
              (%s,'content_domain','海洋文化','manual')
            """,
            (account_id, account_id),
        )

        cur.execute(
            """
            insert into account_metric_snapshot(
              account_id, provider, source_endpoint, observation_key, captured_at,
              follower_count, following_count, total_favorited, video_count
            )
            values
              (%s,'tikhub','fixture','acct-snap-1',now()-interval '20 days',
               100000,300,4000000,40),
              (%s,'tikhub','fixture','acct-snap-2',now(),
               123500,321,5286000,43)
            """,
            (account_id, account_id),
        )

        for idx, (title, likes, plays, score) in enumerate(
            [
                ("秦皇岛海边惊现龙王祭坛？", 321000, 5286000, 88),
                ("带你沉浸式看渔岛夜景", 186000, 3204000, 72),
                ("海边看到了会喷水的龙", 125000, 2867000, 55),
            ],
            start=1,
        ):
            cur.execute(
                """
                insert into source_video(
                  platform, platform_video_id, account_id, title, source_url,
                  published_at, duration_ms, research_level,
                  monitoring_status, monitoring_priority
                )
                values (
                  'douyin',%s,%s,%s,%s,
                  now()-(%s || ' days')::interval,60000,1,'observe',%s
                )
                returning id
                """,
                (
                    f"acct-video-{idx}",
                    account_id,
                    title,
                    f"https://example.com/acct-video-{idx}",
                    idx,
                    score,
                ),
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
                  %s,'tikhub','fixture',%s,now(),
                  %s,%s,%s,%s,123500
                )
                """,
                (
                    video_id,
                    f"acct-video-metric-{idx}",
                    plays,
                    likes,
                    likes // 20,
                    likes // 10,
                ),
            )

            cur.execute(
                """
                insert into video_score(video_id, score_type, score, rule_version, components)
                values (%s,'priority',%s,'test','{}'::jsonb)
                """,
                (video_id, score),
            )

        conn.commit()

    return str(account_id)


def test_account_library_filters_trend_and_hot_videos() -> None:
    assert DSN
    account_id = clear_and_seed()
    module = load_backend()

    result = module.main(
        resource_from_dsn(DSN),
        platform="douyin",
        days=30,
        research_level=1,
        content_domain="旅行风景",
        follower_min=100000,
        follower_max=200000,
        account_type="个人",
        location="河北·秦皇岛",
        certification_type="个人认证",
        posts_min=3,
        posts_max=5,
        follower_growth_min=20000,
        follower_growth_max=30000,
        monitoring_status="monitoring",
        query="渔海",
        page=1,
        page_size=10,
        sort="growth_desc",
        selected_account_id=account_id,
    )

    assert result["total"] == 1
    assert len(result["items"]) == 1

    item = result["items"][0]
    assert item["nickname"] == "渔海老张"
    assert item["follower_count"] == 123500
    assert item["posts_count"] == 3
    assert item["follower_growth"] == 23500
    assert item["blackhorse_count"] == 2
    assert set(item["content_domains"]) == {"旅行风景", "海洋文化"}

    detail = result["detail"]
    assert detail["id"] == account_id
    assert len(detail["trend"]) == 2
    assert len(detail["hot_videos"]) == 3
    assert detail["hot_videos"][0]["title"] == "秦皇岛海边惊现龙王祭坛？"
    assert detail["fan_profile_available"] is False
    assert detail["similar_accounts_available"] is False
    assert detail["similar_accounts"] == []


def test_account_detail_exposes_explainable_same_platform_similarity() -> None:
    assert DSN
    account_id = clear_and_seed()
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into source_account(
              platform, platform_account_id, nickname, account_type,
              certification_type, research_level, monitoring_status
            )
            values ('douyin','similar-account','相似海边账号','个人','个人认证',1,'observe')
            returning id
            """
        )
        similar_id = cur.fetchone()[0]
        cur.execute(
            """
            insert into account_tag(account_id, tag_type, tag_value, source)
            values
              (%s,'content_domain','旅行风景','fixture'),
              (%s,'content_domain','海洋文化','fixture')
            """,
            (similar_id, similar_id),
        )
        cur.execute(
            """
            insert into account_metric_snapshot(
              account_id, provider, source_endpoint, observation_key,
              follower_count, video_count
            )
            values (%s,'fixture','fixture','similar-account-snapshot',120000,44)
            """,
            (similar_id,),
        )
        conn.commit()

    result = load_backend().main(
        resource_from_dsn(DSN), platform="douyin", days=30, selected_account_id=account_id
    )

    similar = result["detail"]["similar_accounts"]
    assert result["detail"]["similar_accounts_available"] is True
    assert similar == [
        {
            "id": str(similar_id),
            "nickname": "相似海边账号",
            "similarity_score": 100,
            "evidence_coverage": 100,
            "raw_score": 100,
            "matched_domains": ["旅行风景", "海洋文化"],
            "matched_fields": [
                "content_domains",
                "account_type",
                "certification_type",
                "follower_count",
                "video_count",
            ],
            "components": {
                "content_domains": 45,
                "account_type": 15,
                "certification_type": 10,
                "follower_count": 20,
                "video_count": 10,
            },
        }
    ]


def test_account_growth_is_null_with_only_one_snapshot() -> None:
    assert DSN
    account_id = clear_and_seed()
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            delete from account_metric_snapshot
            where account_id=%s::uuid
              and observation_key='acct-snap-1'
            """,
            (account_id,),
        )
        conn.commit()

    module = load_backend()
    result = module.main(
        resource_from_dsn(DSN),
        platform="douyin",
        days=30,
        selected_account_id=account_id,
    )

    assert result["items"][0]["follower_growth"] is None
    assert len(result["detail"]["trend"]) == 1


def test_account_library_empty_search() -> None:
    assert DSN
    clear_and_seed()
    module = load_backend()

    result = module.main(
        resource_from_dsn(DSN),
        platform="douyin",
        days=30,
        query="不存在的账号",
    )

    assert result["total"] == 0
    assert result["items"] == []
    assert result["detail"] == {}
