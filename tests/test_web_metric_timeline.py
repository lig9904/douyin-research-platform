from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import psycopg
import pytest


DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not configured")


def _backend(name: str):
    path = Path("windmill/f/content_research/research_dashboard.raw_app/backend") / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _resource() -> dict[str, object]:
    assert DSN
    parsed = urlparse(DSN)
    return {
        "host": parsed.hostname or "127.0.0.1",
        "port": parsed.port or 5432,
        "user": parsed.username or "",
        "password": parsed.password or "",
        "dbname": parsed.path.lstrip("/"),
        "sslmode": "disable",
    }


def test_metric_timeline_bounds_and_hides_provider_columns() -> None:
    assert DSN
    video_id = uuid4()
    observation_prefix = f"web-timeline-{uuid4()}"
    try:
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into source_video(id, platform, platform_video_id, title)
                values (%s, 'douyin', %s, 'timeline fixture')
                """,
                (video_id, str(video_id)),
            )
            for index, hours in enumerate((1, 4, 8)):
                cur.execute(
                    """
                    insert into metric_snapshot(
                      video_id, provider, source_endpoint, observation_key,
                      play_count, like_count, comment_count, share_count,
                      collect_count, raw_metrics, captured_at
                    ) values (%s, 'private-provider', %s, %s,
                      %s, %s, %s, %s, %s, '{"private":"never return"}'::jsonb,
                      now() - (%s || ' hours')::interval)
                    """,
                    (video_id, ('douyin.billboard.low_fan', 'douyin.app.multi_video_v2', 'private-endpoint')[index], f"{observation_prefix}-{index}", (100, 0, None)[index], 10 + index, index, index, index, hours),
                )
            conn.commit()

        result = _backend("get_video_metric_timeline.py").main(
            _resource(), str(video_id), days=999, page=1, page_size=1
        )
        assert result["days"] == 90
        assert result["page_size"] == 10
        assert result["total"] == 3
        assert len(result["items"]) == 3
        assert [item['source_kind'] for item in result['items']] == ['billboard', 'detail', 'other']
        assert [item['play_count'] for item in result['items']] == [100, 0, None]
        assert 'private-endpoint' not in str(result)
        assert result["excluded_fields"] == ["provider", "source_endpoint", "observation_key", "raw_metrics"]
        assert not {"provider", "source_endpoint", "observation_key", "raw_metrics"} & set(result["items"][0])
        library = _backend("get_video_library.py")
        detail = library.main(_resource(), selected_video_id=str(video_id))["detail"]
        assert detail["metric_source_kind"] == "merged"
        assert detail["play_count"] == 100
        with psycopg.connect(DSN) as conn:
            conn.execute("update metric_snapshot set captured_at=now() where video_id=%s and source_endpoint='douyin.app.multi_video_v2'", (video_id,))
        detail = library.main(_resource(), selected_video_id=str(video_id))["detail"]
        assert detail["metric_source_kind"] == "merged"
        assert detail["play_count"] == 100
        assert detail["like_count"] == 11
        assert detail["metric_provenance"]["play_count"]["source_kind"] == "billboard"
        assert detail["metric_provenance"]["like_count"]["source_kind"] == "detail"
        assert "source_endpoint" not in detail
        filtered = library.main(_resource(), query='timeline fixture', play_min=100, play_max=100)
        merged_item = next(item for item in filtered['items'] if item['id'] == str(video_id))
        assert merged_item['play_count'] == detail['play_count']
        assert merged_item['metric_provenance'] == detail['metric_provenance']
    finally:
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute("delete from source_video where id=%s", (video_id,))
            conn.commit()
