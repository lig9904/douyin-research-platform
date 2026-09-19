from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import psycopg
import pytest


ROOT = Path(__file__).parents[1]
BACKEND = ROOT / "windmill/f/content_research/research_dashboard.raw_app/backend"
DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not configured")


def _load(stem: str):
    path = BACKEND / f"{stem}.py"
    spec = importlib.util.spec_from_file_location(f"{stem}_backend", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mutate = _load("mutate_research_state")
read_state = _load("get_research_user_state")


def _resource(dsn: str) -> dict[str, object]:
    parsed = urlparse(dsn)
    return {
        "host": parsed.hostname or "127.0.0.1",
        "port": parsed.port or 5432,
        "user": parsed.username or "",
        "password": parsed.password or "",
        "dbname": parsed.path.lstrip("/"),
        "sslmode": "disable",
    }


def _seed() -> dict[str, str]:
    assert DSN
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        for table in (
            "research_user_action",
            "saved_research_filter",
            "collection_item",
            "collection",
            "external_signal",
            "source_video",
            "source_account",
        ):
            cur.execute(f"delete from {table}")
        cur.execute(
            """
            insert into source_account(platform, platform_account_id, nickname)
            values ('douyin','action-account','安全账号') returning id
            """
        )
        account_id = str(cur.fetchone()[0])
        cur.execute(
            """
            insert into source_video(platform, platform_video_id, account_id, title)
            values ('douyin','action-video',%s,'安全视频') returning id
            """,
            (account_id,),
        )
        video_id = str(cur.fetchone()[0])
        cur.execute(
            """
            insert into external_signal(
              provider, platform, signal_type, provider_signal_id, signal_key, title
            ) values (
              'fixture','douyin','hot_topic','action-signal','action-signal-key','安全热点'
            )
            returning id
            """
        )
        signal_id = str(cur.fetchone()[0])
        conn.commit()
    return {"video": video_id, "account": account_id, "signal": signal_id}


def _call(db, **kwargs):
    defaults = {
        "asset_type": "",
        "asset_ids": [],
        "monitoring_status": "",
        "monitoring_priority": None,
        "collection_name": "",
        "note": "",
        "view_key": "",
        "filter_name": "",
        "filters_json": "{}",
    }
    defaults.update(kwargs)
    return mutate.main(db=db, **defaults)


def test_research_actions_require_end_user_identity(monkeypatch) -> None:
    monkeypatch.delenv("WM_END_USER_EMAIL", raising=False)
    with pytest.raises(PermissionError, match="IDENTITY_REQUIRED"):
        mutate.main({}, "save_filter", str(uuid4()), view_key="videos")
    with pytest.raises(PermissionError, match="IDENTITY_REQUIRED"):
        read_state.main({}, "videos")


def test_collections_monitoring_filters_and_idempotency(monkeypatch) -> None:
    assert DSN
    ids = _seed()
    db = _resource(DSN)
    monkeypatch.setenv("WM_END_USER_EMAIL", "Researcher@Example.com")

    for asset_type, asset_id in ids.items():
        result = _call(
            db,
            action="add_to_collection",
            idempotency_key=str(uuid4()),
            asset_type=asset_type,
            asset_ids=[asset_id],
            collection_name="本机验收专题",
            note="有限公开研究样本",
        )
        assert result["changed_count"] == 1
        assert result["external_calls"] == result["llm_calls"] == 0

    monitor_key = str(uuid4())
    monitored = _call(
        db,
        action="set_monitoring",
        idempotency_key=monitor_key,
        asset_type="video",
        asset_ids=[ids["video"]],
        monitoring_status="follow_up",
        monitoring_priority=75,
    )
    replay = _call(
        db,
        action="set_monitoring",
        idempotency_key=monitor_key,
        asset_type="video",
        asset_ids=[ids["video"]],
        monitoring_status="follow_up",
        monitoring_priority=75,
    )
    assert monitored["idempotent_replay"] is False
    assert replay["idempotent_replay"] is True
    assert replay["db_writes"] == 0

    with pytest.raises(mutate.ResearchActionConflict, match="IDEMPOTENCY_CONFLICT"):
        _call(
            db,
            action="set_monitoring",
            idempotency_key=monitor_key,
            asset_type="video",
            asset_ids=[ids["video"]],
            monitoring_status="stopped",
        )

    saved = _call(
        db,
        action="save_filter",
        idempotency_key=str(uuid4()),
        view_key="videos",
        filter_name="近30天重点",
        filters_json=json.dumps(
            {"platform": "douyin", "days": 30, "priority_min": 60},
            ensure_ascii=False,
        ),
    )
    assert saved["changed_count"] == 1

    state = read_state.main(db, "videos")
    serialized = json.dumps(state, ensure_ascii=False)
    assert state["collections"] == [
        {
            "name": "本机验收专题",
            "video_count": 1,
            "account_count": 1,
            "signal_count": 1,
        }
    ]
    assert state["saved_filters"][0]["name"] == "近30天重点"
    assert state["saved_filters"][0]["filters"]["priority_min"] == 60
    assert "researcher@example.com" not in serialized

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "select monitoring_status, monitoring_priority from source_video where id=%s",
            (ids["video"],),
        )
        assert cur.fetchone() == ("follow_up", 75)
        cur.execute("select count(*) from research_user_action")
        assert cur.fetchone()[0] == 5


def test_invalid_or_missing_assets_leave_no_audit_record(monkeypatch) -> None:
    assert DSN
    _seed()
    db = _resource(DSN)
    monkeypatch.setenv("WM_END_USER_EMAIL", "researcher@example.com")
    key = str(uuid4())

    with pytest.raises(mutate.ResearchActionError, match="do not exist"):
        _call(
            db,
            action="set_monitoring",
            idempotency_key=key,
            asset_type="video",
            asset_ids=[str(uuid4())],
            monitoring_status="follow_up",
        )

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("select count(*) from research_user_action where idempotency_key=%s", (key,))
        assert cur.fetchone()[0] == 0


def test_filter_payload_is_strictly_bounded(monkeypatch) -> None:
    monkeypatch.setenv("WM_END_USER_EMAIL", "researcher@example.com")
    with pytest.raises(mutate.ResearchActionError, match="filters_json is invalid"):
        mutate._filters("videos", json.dumps({"provider_secret": "never-store"}))
    with pytest.raises(mutate.ResearchActionError, match="filters_json is invalid"):
        mutate._filters("videos", json.dumps({"query": {"nested": "not allowed"}}))
