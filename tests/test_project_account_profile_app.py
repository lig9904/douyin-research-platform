from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "windmill/f/content_research/research_dashboard.raw_app/backend/refresh_project_account_profile.py"
)
spec = importlib.util.spec_from_file_location("refresh_project_account_profile_app", SCRIPT)
assert spec is not None and spec.loader is not None
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

DB = {
    "host": "127.0.0.1", "port": 5432, "user": "test",
    "password": "fixture", "dbname": "test", "sslmode": "disable",
}
VIDEO_ID = "7658347686323555610"


def test_preview_is_db_only_and_execute_needs_explicit_confirmation(monkeypatch):
    project_id = str(uuid4())
    checks = []
    monkeypatch.setenv("WM_END_USER_EMAIL", "owner@example.com")
    monkeypatch.setattr(app, "_accepted_account", lambda *args: checks.append(args))
    preview = app.main(DB, project_id, VIDEO_ID)
    assert preview["eligible"] is True
    assert preview["external_calls"] == 0
    assert preview["maximum_new_calls"] == 1
    assert len(checks) == 1 and checks[0][-1] == "owner@example.com"
    with pytest.raises(ValueError, match="confirmation"):
        app.main(DB, project_id, VIDEO_ID, action="execute")


def test_actor_identity_is_required_before_access_or_key_read(monkeypatch):
    monkeypatch.delenv("WM_END_USER_EMAIL", raising=False)
    monkeypatch.setattr(app, "_accepted_account", lambda *args: pytest.fail("DB access"))
    with pytest.raises(PermissionError, match="IDENTITY_REQUIRED"):
        app.main(DB, str(uuid4()), VIDEO_ID)


def test_denied_project_never_reads_secret(monkeypatch):
    monkeypatch.setenv("WM_END_USER_EMAIL", "viewer@example.com")
    monkeypatch.setattr(
        app, "_accepted_account",
        lambda *args: (_ for _ in ()).throw(PermissionError("not a project owner")),
    )
    with pytest.raises(PermissionError, match="PROFILE_DENIED"):
        app.main(
            DB, str(uuid4()), VIDEO_ID, action="execute",
            confirmation="REFRESH_PUBLIC_ACCOUNT_PROFILE_PAID",
        )


def test_confirmed_execute_returns_only_cost_and_run_summary(monkeypatch):
    monkeypatch.setenv("WM_END_USER_EMAIL", "owner@example.com")
    monkeypatch.setattr(app, "_accepted_account", lambda *args: None)
    calls = []
    monkeypatch.setitem(__import__("sys").modules, "wmill", SimpleNamespace(
        get_variable=lambda key: calls.append(key) or "fixture-key",
    ))
    monkeypatch.setattr(app, "refresh_project_account_profiles", lambda **kwargs: SimpleNamespace(
        run_id=uuid4(), requested_video_count=1, distinct_account_count=1,
        snapshots_inserted=1, external_calls=1, cached_calls=0,
        estimated_api_cost_usd=0.001,
    ))
    result = app.main(
        DB, str(uuid4()), VIDEO_ID, action="execute",
        confirmation="REFRESH_PUBLIC_ACCOUNT_PROFILE_PAID",
    )
    assert calls == ["f/content_research/tikhub_api_key"]
    assert result["status"] == "completed"
    assert result["external_calls"] == 1
    assert "fixture-key" not in str(result)
