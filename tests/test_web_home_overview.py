from __future__ import annotations

import importlib.util
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo

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
              false,0.001,'USD',now(),now(),'{"cost_basis":"verified_unit_price"}'::jsonb
            )
            """
        )

        cur.execute(
            """
            insert into external_api_call(
              provider, platform, endpoint_key, request_fingerprint, status,
              cached, estimated_cost, actual_cost, cost_currency, started_at, finished_at, metadata
            )
            values (
              'tikhub','douyin','fixture-missing-price','web-call-missing','success',
              false,null,null,'USD',now(),now(),'{"cost_basis":"estimated_unit_price","billing_status":"estimated"}'::jsonb
            )
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
    assert result["kpis"]["api_call_records"] == 2
    assert "budget_usage_pct" not in result["kpis"]
    assert result["api_costs"] == [{
        "currency": "USD",
        "estimated_cost": pytest.approx(0.001),
        "reconciled_cost": pytest.approx(0),
        "known_zero_calls": 0,
        "unknown_cost_calls": 1,
    }]
    assert result["blackhorse"][0]["title"] == "测试黑马视频"
    assert float(result["blackhorse"][0]["priority"]) == pytest.approx(88)
    assert result["keywords"][0]["keyword"] == "海边传说"
    assert result["ip_adaptation"]["status"] == "pending"
    assert "尚未启用" not in result["ip_adaptation"]["message"]
    assert "视频详情" in result["ip_adaptation"]["message"]

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


def test_home_returns_supplier_account_total_independent_of_selected_platform() -> None:
    assert DSN
    scope = f"home-daily-{uuid4()}"
    billing_date = datetime.now(timezone.utc).astimezone(ZoneInfo("America/Los_Angeles")).date()
    try:
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into supplier_daily_spend(
                  provider, account_scope, billing_date, cost_currency, billing_timezone,
                  total_cost, balance_cost, free_credit_cost, total_requests, paid_requests, fetched_at
                ) values ('tikhub', %s, %s, 'USD', 'America/Los_Angeles',
                  0.052, 0.052, 0, 3, 2, now())
                """,
                (scope, billing_date),
            )
            conn.commit()
        result = load_backend().main(resource_from_dsn(DSN), platform="kuaishou", hours=24)
        spend = result["supplier_daily_spend"]
        row = next(item for item in spend["today"] if item["account_scope"] == scope)
        assert spend["status"] == "available"
        assert row["billing_date"] == billing_date.isoformat()
        assert row["billing_timezone"] == "America/Los_Angeles"
        assert row["period_status"] == "current_accumulating"
        assert row["total_cost"] == pytest.approx(0.052)
    finally:
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute("delete from supplier_daily_spend where account_scope=%s", (scope,))
            conn.commit()
