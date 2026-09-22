from __future__ import annotations

import importlib.util
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict


ROOT = Path(__file__).parents[1]
BACKEND = ROOT / "windmill/f/content_research/research_dashboard.raw_app/backend"
COLLECTORS = ROOT / "windmill/f/content_research/collectors"
DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not configured")


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mutate = _load(BACKEND / "mutate_research_brief.py", "brief_mutate_integration")
read = _load(BACKEND / "get_research_briefs.py", "brief_read_integration")
runner = _load(COLLECTORS / "run_research_brief.py", "brief_runner_integration")


def _resource() -> dict[str, object]:
    assert DSN
    values = conninfo_to_dict(DSN)
    return {
        "host": values.get("host", "127.0.0.1"),
        "port": int(values.get("port", 5432)),
        "user": values.get("user", "postgres"),
        "password": values.get("password", ""),
        "dbname": values.get("dbname", "postgres"),
        "sslmode": values.get("sslmode", "prefer"),
    }


def _clear() -> None:
    assert DSN
    with psycopg.connect(DSN) as conn:
        conn.execute("delete from research_brief_run")
        conn.execute("delete from research_brief")
        conn.execute("delete from research_user_action")


def _mutate(action: str, *, brief_id: str = "", cadence_hours=None):
    return mutate.main(
        db=_resource(),
        writer_allowlist="researcher@example.com",
        action=action,
        idempotency_key=str(uuid4()),
        brief_id=brief_id,
        name="神话文旅机会",
        platform="douyin",
        source_type="keyword",
        target="龙 传说",
        time_window_hours=72,
        max_items=5,
        depth="comments",
        cadence_hours=cadence_hours,
    )


def test_one_time_brief_lifecycle_is_actor_isolated_and_claimed_once(monkeypatch) -> None:
    assert DSN
    _clear()
    monkeypatch.setenv("WM_END_USER_EMAIL", "Researcher@Example.com")
    created = _mutate("create")
    brief_id = created["brief_id"]
    assert created["brief_status"] == "draft"

    listed = read.main(_resource())
    assert [item["id"] for item in listed["briefs"]] == [brief_id]
    assert listed["runs"] == []

    activated = _mutate("activate", brief_id=brief_id)
    assert activated["brief_status"] == "active"
    claim = runner._claim(DSN, UUID(brief_id), "scheduled-worker")
    assert claim is not None
    assert claim["config"]["target"] == "龙 传说"
    assert claim["config"]["cadence_hours"] is None
    assert runner._claim(DSN, UUID(brief_id), "scheduled-worker") is None

    runner._finish(
        DSN, claim["brief_run_id"], status="success", summary={"external_calls": 0},
    )
    with psycopg.connect(DSN) as conn:
        brief = conn.execute(
            "select status, next_due_at, last_dispatched_at from research_brief where id=%s",
            (brief_id,),
        ).fetchone()
        run = conn.execute(
            "select status, trigger_kind, triggered_by from research_brief_run where id=%s",
            (claim["brief_run_id"],),
        ).fetchone()
    assert brief[0:2] == ("paused", None)
    assert brief[2] is not None
    assert run == ("success", "schedule", "scheduled-worker")

    monkeypatch.setenv("WM_END_USER_EMAIL", "other@example.com")
    assert read.main(_resource())["briefs"] == []


def test_recurring_claim_advances_from_current_time_and_idempotency_replays(monkeypatch) -> None:
    assert DSN
    _clear()
    monkeypatch.setenv("WM_END_USER_EMAIL", "researcher@example.com")
    key = str(uuid4())
    call = dict(
        db=_resource(), writer_allowlist="researcher@example.com", action="create",
        idempotency_key=key, name="每日账号", platform="douyin",
        source_type="account", target="sec-user", time_window_hours=24,
        max_items=5, depth="media", cadence_hours=6,
    )
    first = mutate.main(**call)
    replay = mutate.main(**call)
    assert replay["brief_id"] == first["brief_id"]
    assert replay["idempotent_replay"] is True
    assert replay["db_writes"] == 0

    _mutate("activate", brief_id=first["brief_id"], cadence_hours=6)
    before = datetime.now(timezone.utc)
    claim = runner._claim(DSN, UUID(first["brief_id"]), "scheduled-worker")
    after = datetime.now(timezone.utc)
    assert claim is not None
    with psycopg.connect(DSN) as conn:
        status, next_due = conn.execute(
            "select status, next_due_at from research_brief where id=%s",
            (first["brief_id"],),
        ).fetchone()
    assert status == "active"
    assert before.timestamp() + 6 * 3600 <= next_due.timestamp()
    assert next_due.timestamp() <= after.timestamp() + 6 * 3600 + 2


def test_database_rejects_missing_keyword_target() -> None:
    assert DSN
    _clear()
    with psycopg.connect(DSN) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                """
                insert into research_brief(
                  owner_actor,name,platform,source_type,target,time_window_hours,
                  max_items,depth,cadence_hours
                ) values ('researcher@example.com','invalid','douyin','keyword',null,
                          24,5,'metadata',null)
                """
            )
