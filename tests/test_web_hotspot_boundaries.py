from __future__ import annotations

import importlib.util
import os
import sys
import types
from datetime import datetime
from pathlib import Path

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


@pytest.fixture
def context():
    # Reuse the existing deterministic fixture, only in the disposable CI DB.
    path = Path("tests/test_web_hotspot_library.py")
    spec = importlib.util.spec_from_file_location("hotspot_test_fixtures", path)
    assert spec and spec.loader
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    assert DSN
    signal_id = helper.clear_and_seed()
    return helper.load_backend(), helper.resource_from_dsn(DSN), signal_id


def read_library(context, **overrides):
    module, resource, signal_id = context
    params = {"platform": "douyin", "days": 7, "selected_signal_id": signal_id}
    params.update(overrides)
    return module.main(resource, **params)


def test_same_timestamp_is_one_observation_not_growth(context):
    _, _, signal_id = context
    with psycopg.connect(DSN) as conn:
        conn.execute(
            "update signal_snapshot set captured_at=now()-interval '1 hour' "
            "where signal_id=%s::uuid",
            (signal_id,),
        )
    result = read_library(context)
    assert result["items"][0]["heat_value"] == 2000000
    assert result["items"][0]["heat_growth_pct"] is None
    assert len(result["detail"]["trend"]) == 1
    assert result["detail"]["trend"][0]["heat_value"] == 2000000


def test_growth_uses_previous_distinct_timestamp(context):
    _, _, signal_id = context
    with psycopg.connect(DSN) as conn:
        conn.execute(
            """
            insert into signal_snapshot(signal_id, captured_at, heat_value)
            select signal_id, captured_at, 2500000
            from signal_snapshot
            where signal_id=%s::uuid
            order by captured_at desc, id desc
            limit 1
            """,
            (signal_id,),
        )
    result = read_library(context)
    assert result["items"][0]["heat_value"] == 2500000
    # 2.5M versus yesterday's 1M, not the duplicate 2M at the same time.
    assert result["items"][0]["heat_growth_pct"] == pytest.approx(150.0)
    assert [p["heat_value"] for p in result["detail"]["trend"]] == [1000000, 2500000]


def test_future_observation_is_not_current_or_trend(context):
    _, _, signal_id = context
    with psycopg.connect(DSN) as conn:
        conn.execute(
            "insert into signal_snapshot(signal_id, captured_at, heat_value) "
            "values (%s::uuid, now()+interval '1 day', 9000000)",
            (signal_id,),
        )
    result = read_library(context)
    assert result["items"][0]["heat_value"] == 2000000
    assert result["items"][0]["heat_growth_pct"] == pytest.approx(100.0)
    assert len(result["detail"]["trend"]) == 2


def test_trend_limit_keeps_newest_240_real_observations(context):
    _, _, signal_id = context
    with psycopg.connect(DSN) as conn:
        conn.execute("delete from signal_snapshot where signal_id=%s::uuid", (signal_id,))
        conn.execute(
            """
            insert into signal_snapshot(signal_id, captured_at, heat_value)
            select %s::uuid, now() - ((261-n) * interval '1 minute'), n
            from generate_series(1,260) as points(n)
            """,
            (signal_id,),
        )
    result = read_library(context)
    trend = result["detail"]["trend"]
    assert len(trend) == 240
    assert [p["heat_value"] for p in trend] == list(range(21, 261))
    times = [datetime.fromisoformat(p["captured_at"]) for p in trend]
    assert times == sorted(times)
    assert len(times) == len(set(times))
    assert trend[-1]["heat_value"] == result["items"][0]["heat_value"]
    assert trend[-1]["captured_at"] == result["items"][0]["snapshot_captured_at"]


@pytest.mark.parametrize("latest_value,expected_total", [(None, 0), (0, 1)])
def test_missing_heat_is_not_coerced_to_measured_zero(context, latest_value, expected_total):
    _, _, signal_id = context
    with psycopg.connect(DSN) as conn:
        conn.execute(
            "update signal_snapshot set heat_value=%s where signal_id=%s::uuid",
            (latest_value, signal_id),
        )
    result = read_library(context, heat_min=0)
    assert result["total"] == expected_total
    if expected_total:
        assert result["items"][0]["heat_value"] == 0
        assert result["items"][0]["heat_growth_pct"] is None
    else:
        assert result["items"] == []
        assert result["detail"] == {}


@pytest.mark.parametrize("previous_value", [0, None])
def test_zero_or_missing_growth_baseline_remains_unknown(context, previous_value):
    _, _, signal_id = context
    with psycopg.connect(DSN) as conn:
        conn.execute(
            """
            update signal_snapshot set heat_value=%s
            where signal_id=%s::uuid and captured_at < (
              select max(captured_at) from signal_snapshot where signal_id=%s::uuid
            )
            """,
            (previous_value, signal_id, signal_id),
        )
    result = read_library(context)
    assert result["items"][0]["heat_value"] == 2000000
    assert result["items"][0]["heat_growth_pct"] is None


@pytest.mark.parametrize(
    "filters",
    [
        {"platform": "bilibili"},
        {"query": "不存在的热点"},
        {"category": "不存在的分类"},
        {"page": 99},
    ],
)
def test_stale_selection_cannot_escape_visible_filtered_page(context, filters):
    result = read_library(context, **filters)
    assert result["items"] == []
    assert result["detail"] == {}


@pytest.mark.parametrize("selected_id", ["not-a-uuid", "00000000-0000-0000-0000-000000000000"])
def test_invalid_or_missing_selected_id_falls_back_to_visible_item(context, selected_id):
    result = read_library(context, selected_signal_id=selected_id)
    assert result["total"] == 1
    assert result["detail"]["id"] == context[2]


def test_multiple_relations_do_not_duplicate_related_video(context):
    _, _, signal_id = context
    with psycopg.connect(DSN) as conn:
        conn.execute(
            """
            insert into signal_video_link(signal_id, video_id, relation_type, observed_at)
            select signal_id, video_id, 'mentioned', now()
            from signal_video_link where signal_id=%s::uuid
            limit 1
            """,
            (signal_id,),
        )
    result = read_library(context)
    assert result["items"][0]["related_video_count"] == 1
    assert len(result["detail"]["related_videos"]) == 1


@pytest.mark.parametrize(
    "sort",
    ["heat_desc", "growth_desc", "discussion_desc", "related_desc", "recent_desc", "rank_asc"],
)
def test_tied_sort_values_have_stable_pagination(context, sort):
    with psycopg.connect(DSN) as conn:
        rows = conn.execute(
            """
            insert into external_signal(provider, platform, signal_type, signal_key, title)
            select 'fixture','douyin','hot_topic','tie-fixture-' || n,'tie-fixture'
            from generate_series(1,13) as signals(n)
            returning id
            """
        ).fetchall()
        expected = sorted(str(row[0]) for row in rows)
        conn.execute(
            """
            insert into signal_snapshot(signal_id, captured_at, heat_value)
            select id, now(), 42 from external_signal where provider='fixture'
            """
        )
    first = read_library(context, query="tie-fixture", sort=sort, page=1, page_size=10)
    second = read_library(context, query="tie-fixture", sort=sort, page=2, page_size=10)
    assert first["total"] == second["total"] == 13
    assert [item["id"] for item in first["items"]] == expected[:10]
    assert [item["id"] for item in second["items"]] == expected[10:]
