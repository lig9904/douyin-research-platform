from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "windmill/f/content_research/research_dashboard.raw_app/backend/refresh_project_video_statistics.py"
)
spec = importlib.util.spec_from_file_location("refresh_project_video_statistics_app", SCRIPT)
assert spec is not None and spec.loader is not None
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

DB = {
    "host": "127.0.0.1", "port": 5432, "user": "test",
    "password": "fixture", "dbname": "test", "sslmode": "disable",
}
VIDEO_ID = "7658347686323555610"


class _Connection:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def cursor(self):
        return self


def test_preview_is_database_only_and_execute_needs_confirmation(monkeypatch):
    project_id = str(uuid4())
    checks = []
    monkeypatch.setenv("WM_END_USER_EMAIL", "owner@example.com")
    monkeypatch.setattr(__import__("psycopg"), "connect", lambda *_args: _Connection())
    monkeypatch.setattr(app, "_accepted_videos", lambda *args: checks.append(args))
    preview = app.main(DB, project_id, [VIDEO_ID])
    assert preview["eligible"] is True and preview["external_calls"] == 0
    assert preview["maximum_new_calls"] == 1
    assert len(checks) == 1 and checks[0][2] == (VIDEO_ID,)
    with pytest.raises(ValueError, match="confirmation"):
        app.main(DB, project_id, [VIDEO_ID], action="execute")


def test_invalid_identity_and_ids_stop_before_database_or_secret(monkeypatch):
    monkeypatch.delenv("WM_END_USER_EMAIL", raising=False)
    with pytest.raises(PermissionError, match="IDENTITY_REQUIRED"):
        app.main(DB, str(uuid4()), [VIDEO_ID])
    monkeypatch.setenv("WM_END_USER_EMAIL", "owner@example.com")
    with pytest.raises(ValueError, match="exact Douyin video IDs"):
        app.main(DB, str(uuid4()), [[VIDEO_ID]])


def test_denied_project_does_not_read_secret(monkeypatch):
    monkeypatch.setenv("WM_END_USER_EMAIL", "owner@example.com")
    monkeypatch.setattr(__import__("psycopg"), "connect", lambda *_args: _Connection())
    monkeypatch.setattr(
        app, "_accepted_videos",
        lambda *_args: (_ for _ in ()).throw(PermissionError("not accepted")),
    )
    monkeypatch.setitem(sys.modules, "wmill", SimpleNamespace(
        get_variable=lambda *_args: pytest.fail("secret read"),
    ))
    with pytest.raises(PermissionError, match="VIDEO_STATISTICS_DENIED"):
        app.main(
            DB, str(uuid4()), [VIDEO_ID], action="execute",
            confirmation="REFRESH_PUBLIC_VIDEO_STATISTICS_PAID",
        )


def test_execute_returns_only_bound_metrics_and_cost(monkeypatch):
    monkeypatch.setenv("WM_END_USER_EMAIL", "owner@example.com")
    monkeypatch.setattr(__import__("psycopg"), "connect", lambda *_args: _Connection())
    monkeypatch.setattr(app, "_accepted_videos", lambda *_args: None)
    calls = []
    monkeypatch.setitem(sys.modules, "wmill", SimpleNamespace(
        get_variable=lambda key: calls.append(key) or "fixture-key",
    ))
    monkeypatch.setattr(app, "refresh_project_video_statistics", lambda **kwargs: SimpleNamespace(
        run_id=uuid4(), play_counts={VIDEO_ID: 1234}, snapshots_inserted=1,
        external_calls=1, cached_calls=0, estimated_api_cost_usd=0.001,
    ))
    result = app.main(
        DB, str(uuid4()), [VIDEO_ID], action="execute",
        confirmation="REFRESH_PUBLIC_VIDEO_STATISTICS_PAID",
    )
    assert calls == ["f/content_research/tikhub_api_key"]
    assert result["play_counts"] == {VIDEO_ID: 1234}
    assert result["snapshots_inserted"] == 1
    assert "fixture-key" not in str(result)
