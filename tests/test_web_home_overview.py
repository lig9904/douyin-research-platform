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
        "backend/get_home_overview.py"
    )
    spec = importlib.util.spec_from_file_location("get_home_overview", path)
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


def clear_and_seed() -> None:
    assert DSN
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        for table in (
            "video_score",
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
            values ('douyin','acct-home','测试账号')
            returning id
            """
        )
        account_id = cur.fetchone()[0]

        cur.execute(
            """
            insert into source_video(
              platform, platform_video_id, account_id, title, research_level,
              monitoring_priority, first_seen_at, last_seen_at
            )
            values (
              'douyin','video-home',%s,'测试黑马视频',1,88,
              now()-interval '2 hours',now()-interval '1 hour'
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
            values (%s,'tikhub','fixture','web-account-metric',1000,now())
            """,
            (account_id,),
        )

        cur.execute(
            """
            insert into metric_snapshot(
              video_id, provider, source_endpoint, observation_key,
              like_count, comment_count, share_count, author_follower_count, captured_at
            )
            values (%s,'tikhub','fixture','web-video-metric',5000,500,200,1000,now())
            """,
            (video_id,),
        )

        cur.execute(
            """
            insert into video_score(video_id, score_type, score, rule_version, components)
            values (%s,'priority',88,'test', '{}'::jsonb)
            """,
            (video_id,),
        )

        cur.execute(
            """
            insert into discovery_event(
              video_id, provider, source_type, source_key, observation_key,
              discovered_at, rank_value, rule_version, metadata
            )
            values (%s,'tikhub','low_fan','24h','web-discovery',now(),1,'test','{}'::jsonb)
            """,
            (video_id,),
        )

        cur.execute(
            """
            insert into external_signal(
              provider, platform, signal_type, signal_key, title, first_seen_at, last_seen_at
            )
            values ('tikhub','douyin','hot_search','hot-1','海边传说',now(),now())
            """
        )

        cur.execute(
            """
            insert into external_api_call(
              provider, platform, endpoint_key, request_fingerprint, status,
              cached, actual_cost, cost_currency, started_at, finished_at, metadata
            )
            values (
              'tikhub','douyin','fixture','web-call','success',
              false,0.001,'USD',now(),now(),'{}'::jsonb
            )
            """
        )

        cur.execute(
            """
            insert into daily_budget(
              budget_date, provider, budget_key, max_cost, max_requests,
              spent_cost, used_requests
            )
            values (current_date,'tikhub','l0l1',1,1000,0.1,10)
            """
        )

        cur.execute(
            """
            insert into pipeline_run(
              run_type, run_version, platform, status, started_at, finished_at,
              input_count, output_count, promoted_l1_count, summary
            )
            values (
              'l0l1_discovery','test','douyin','success',
              now()-interval '30 minutes',now()-interval '29 minutes',
              1,1,1,'{"llm_calls":0}'::jsonb
            )
            """
        )
        conn.commit()


def test_home_backend_returns_real_multiplatform_dashboard_shape() -> None:
    assert DSN
    clear_and_seed()
    module = load_backend()

    result = module.main(resource_from_dsn(DSN), platform="douyin", hours=24)

    assert result["selected_platform"] == "douyin"
    assert result["kpis"]["new_hotspots"] == 1
    assert result["kpis"]["blackhorse_candidates"] == 1
    assert result["kpis"]["entered_l1"] == 1
    assert float(result["kpis"]["api_cost_usd"]) == pytest.approx(0.001)
    assert result["blackhorse"][0]["title"] == "测试黑马视频"
    assert float(result["blackhorse"][0]["priority"]) == pytest.approx(88)
    assert result["keywords"][0]["keyword"] == "海边传说"
    assert result["ip_adaptation"]["status"] == "pending"

    keys = [x["key"] for x in result["platforms"]]
    assert keys[:6] == [
        "douyin",
        "kuaishou",
        "wechat_channels",
        "xiaohongshu",
        "bilibili",
        "weibo",
    ]


def test_home_backend_all_platforms_works() -> None:
    assert DSN
    clear_and_seed()
    module = load_backend()

    result = module.main(resource_from_dsn(DSN), platform="all", hours=24)

    assert result["selected_platform"] == "all"
    assert result["kpis"]["blackhorse_candidates"] == 1
