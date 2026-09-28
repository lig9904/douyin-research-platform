from pathlib import Path
from contextlib import nullcontext
from importlib import util
import os
import re
import sys
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest


ROOT = Path(__file__).resolve().parents[1]
LAS = ROOT / "windmill/f/content_research/project_las"
APP = ROOT / "windmill/f/content_research/research_dashboard.raw_app"
BACKEND = APP / "backend/project_las_video_review.py"
PIN = re.compile(r"douyin-research-platform@([0-9a-f]{40})")


def test_project_las_windmill_scripts_share_exact_released_core_and_no_browser_secret() -> None:
    sources = [BACKEND, *(LAS / f"{stem}.py" for stem in
                          ("dispatch_video", "poll_video", "run_pending"))]
    pins = []
    for source in sources:
        text = source.read_text(encoding="utf-8")
        assert '# requires-python = "==3.14.*"' in text
        assert "wmill==1.815.0" in text
        match = PIN.search(text)
        assert match, source
        pins.append(match.group(1))
    assert len(set(pins)) == 1
    front = (APP / "src/components/ProjectLASVideoReviewPanel.tsx").read_text(encoding="utf-8")
    backend = BACKEND.read_text(encoding="utf-8")
    assert "api_key" not in backend
    assert "api_key" not in front
    assert "action: 'prepare'" in front
    assert "Modal.confirm" in front
    assert "las_video_understanding_api_key" in (LAS / "dispatch_video.py").read_text()
    assert "las_video_understanding_api_key" in (LAS / "poll_video.py").read_text()


def test_project_las_generated_locks_bind_the_same_core_and_workspace_hashes() -> None:
    sources = [BACKEND, *(LAS / f"{stem}.py" for stem in
                          ("dispatch_video", "poll_video", "run_pending"))]
    workspace_lock = (ROOT / "windmill/wmill-lock.yaml").read_text(encoding="utf-8")
    for source in sources:
        pin = PIN.search(source.read_text(encoding="utf-8"))
        assert pin is not None, source
        dependency_lock = source.with_suffix(
            ".lock" if source == BACKEND else ".script.lock"
        )
        locked = dependency_lock.read_text(encoding="utf-8")
        assert "# py: 3.14" in locked[:100], dependency_lock
        assert f"douyin-research-platform@{pin.group(1)}" in locked, dependency_lock
        assert "psycopg==3.3.6" in locked, dependency_lock
        assert "wmill==1.815.0" in locked, dependency_lock
        if source == BACKEND:
            key = "f/content_research/research_dashboard.raw_app+project_las_video_review.py"
        else:
            key = "f/content_research/project_las/" + source.stem
        assert re.search(rf"(?m)^  {re.escape(key)}: [0-9a-f]{{64}}$", workspace_lock), source


def test_project_las_scheduler_only_selects_explicit_prepared_tasks_and_existing_refs() -> None:
    scheduler = (LAS / "run_pending.py").read_text(encoding="utf-8")
    assert "attempt.status='prepared'" in scheduler
    assert "review.status='approved'" in scheduler
    assert "status in ('submitted','running','unknown')" in scheduler
    assert "provider_task_ref is not null" in scheduler
    assert "provider_task_recorded_at > now()-interval '70 hours'" in scheduler
    assert "provider_task_recorded_at <= now()-interval '70 hours'" in scheduler
    assert "error_code is distinct from 'provider_task_expiry_near'" in scheduler
    assert "limit 4" in scheduler and "limit 8" in scheduler
    poll = (LAS / "poll_video.py").read_text(encoding="utf-8")
    assert "media_storage_config" not in poll
    schedule = (LAS / "run_pending.schedule.yaml").read_text(encoding="utf-8")
    assert "enabled: false" in schedule


def test_project_las_batch_failure_names_exact_attempt_without_provider_details(monkeypatch) -> None:
    spec = util.spec_from_file_location("las_batch_failure_test", LAS / "run_pending.py")
    assert spec and spec.loader
    scheduler = util.module_from_spec(spec)
    spec.loader.exec_module(scheduler)
    attempt_id = "11111111-1111-4111-8111-111111111111"
    scheduler._pending = lambda dsn, account: ([attempt_id], [])
    scheduler._unresolved_claims = lambda dsn, account: ([], 0)
    scheduler._single_runner = lambda dsn, account: nullcontext(True)
    monkeypatch.setitem(sys.modules, "wmill", SimpleNamespace(
        get_variable=lambda path: ("trusted@example.com" if path.endswith("worker_email")
                                   else "test-account"),
        get_resource=lambda path: {"host": "127.0.0.1", "user": "unused",
                                   "password": "unused", "dbname": "unused"},
        run_script=lambda **kwargs: {"status": "failed"},
    ))
    monkeypatch.setenv("WM_SCHEDULE_PATH", "f/content_research/project_las/run_pending")
    monkeypatch.setenv("WM_EMAIL", "trusted@example.com")
    monkeypatch.delenv("WM_END_USER_EMAIL", raising=False)
    with pytest.raises(RuntimeError, match="project LAS batch known terminal failures") as error:
        scheduler.main()
    assert f"dispatch:{attempt_id}:failed" in str(error.value)
    assert "needs reconciliation" not in str(error.value)
    assert "unused" not in str(error.value)


def test_project_las_batch_mixed_known_failure_and_unknown_keeps_categories(monkeypatch) -> None:
    spec = util.spec_from_file_location("las_batch_mixed_test", LAS / "run_pending.py")
    assert spec and spec.loader
    scheduler = util.module_from_spec(spec)
    spec.loader.exec_module(scheduler)
    failed_id = "11111111-1111-4111-8111-111111111111"
    unknown_id = "22222222-2222-4222-8222-222222222222"
    scheduler._pending = lambda dsn, account: ([failed_id], [unknown_id])
    scheduler._unresolved_claims = lambda dsn, account: ([], 0)

    def run_script(**kwargs):
        status = "failed" if kwargs["args"]["attempt_id"] == failed_id else "unknown"
        return {"status": status}

    with pytest.raises(RuntimeError, match="project LAS batch needs reconciliation") as error:
        scheduler._run_batch("unused", "test-account", SimpleNamespace(run_script=run_script))
    assert f"poll:{unknown_id}:unknown" in str(error.value)
    assert f"dispatch:{failed_id}:failed" in str(error.value)
    assert "known terminal failures" in str(error.value)


def test_project_las_batch_exception_is_not_logged_or_replayed_inline(monkeypatch) -> None:
    spec = util.spec_from_file_location("las_batch_exception_test", LAS / "run_pending.py")
    assert spec and spec.loader
    scheduler = util.module_from_spec(spec)
    spec.loader.exec_module(scheduler)
    attempt_id = "11111111-1111-4111-8111-111111111111"
    scheduler._pending = lambda dsn, account: ([attempt_id], [])
    scheduler._unresolved_claims = lambda dsn, account: ([], 0)
    scheduler._single_runner = lambda dsn, account: nullcontext(True)
    calls: list[str] = []

    def run_script(**kwargs):
        calls.append(kwargs["args"]["attempt_id"])
        raise RuntimeError("private signed URL and supplier key must stay hidden")

    monkeypatch.setitem(sys.modules, "wmill", SimpleNamespace(
        get_variable=lambda path: ("trusted@example.com" if path.endswith("worker_email")
                                   else "test-account"),
        get_resource=lambda path: {"host": "127.0.0.1", "user": "unused",
                                   "password": "unused", "dbname": "unused"},
        run_script=run_script,
    ))
    monkeypatch.setenv("WM_SCHEDULE_PATH", "f/content_research/project_las/run_pending")
    monkeypatch.setenv("WM_EMAIL", "trusted@example.com")
    monkeypatch.delenv("WM_END_USER_EMAIL", raising=False)
    with pytest.raises(RuntimeError, match=f"dispatch:{attempt_id}:needs_attention") as error:
        scheduler.main()
    assert calls == [attempt_id]
    assert "private signed URL" not in str(error.value)
    assert "supplier key" not in str(error.value)


def test_project_las_batch_overlap_never_selects_or_runs_child(monkeypatch) -> None:
    spec = util.spec_from_file_location("las_batch_overlap_test", LAS / "run_pending.py")
    assert spec and spec.loader
    scheduler = util.module_from_spec(spec)
    spec.loader.exec_module(scheduler)
    scheduler._single_runner = lambda dsn, account: nullcontext(False)
    scheduler._unresolved_claims = lambda dsn, account: pytest.fail("overlapping batch scanned claims")
    scheduler._pending = lambda dsn, account: pytest.fail("overlapping batch selected work")
    monkeypatch.setitem(sys.modules, "wmill", SimpleNamespace(
        get_variable=lambda path: ("trusted@example.com" if path.endswith("worker_email")
                                   else "test-account"),
        get_resource=lambda path: {"host": "127.0.0.1", "user": "unused",
                                   "password": "unused", "dbname": "unused"},
        run_script=lambda **kwargs: pytest.fail("overlapping batch started child"),
    ))
    monkeypatch.setenv("WM_SCHEDULE_PATH", "f/content_research/project_las/run_pending")
    monkeypatch.setenv("WM_EMAIL", "trusted@example.com")
    monkeypatch.delenv("WM_END_USER_EMAIL", raising=False)
    assert scheduler.main() == {
        "selected_dispatch": 0, "selected_poll": 0,
        "results": [], "skipped_overlapping": True,
    }


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL is required")
def test_project_las_stale_claim_is_quarantined_and_never_resubmitted(monkeypatch) -> None:
    from tests.test_project_las_live_service import (
        FakeProvider, FakeStorage, _approve_and_prepare, _database, _services,
    )

    monkeypatch.setenv("WM_END_USER_EMAIL", "owner@example.com")
    spec = util.spec_from_file_location("las_stale_claim_test", LAS / "run_pending.py")
    assert spec and spec.loader
    scheduler = util.module_from_spec(spec)
    spec.loader.exec_module(scheduler)
    with _database() as (dsn, conn, project, video, asset):
        human, worker = _services(dsn)
        _, prepared = _approve_and_prepare(human, project, video, asset, "las-video-v2")
        attempt_id = prepared["attempt_id"]
        conn.execute(
            """update project_las_analysis_attempt
                  set status='submitting',submission_count=1 where id=%s""",
            (attempt_id,),
        )
        conn.execute("alter table project_las_analysis_attempt disable trigger trg_project_las_attempt_lifecycle")
        try:
            conn.execute(
                """update project_las_analysis_attempt
                      set updated_at=now()-interval '16 minutes' where id=%s""",
                (attempt_id,),
            )
        finally:
            conn.execute("alter table project_las_analysis_attempt enable trigger trg_project_las_attempt_lifecycle")
        # A still-running provider transaction owns the row lock and must not
        # be quarantined by the scheduled recovery scan.
        with psycopg.connect(dsn) as active:
            active.execute("select id from project_las_analysis_attempt where id=%s for update",
                           (attempt_id,))
            assert scheduler._unresolved_claims(dsn, "test-account") == ([], 0)
        assert scheduler._unresolved_claims(dsn, "other-account") == ([], 0)
        assert scheduler._unresolved_claims(dsn, "test-account") == ([attempt_id], 1)
        row = conn.execute(
            """select status,submission_count,provider_task_ref,error_code
                 from project_las_analysis_attempt where id=%s""", (attempt_id,),
        ).fetchone()
        assert row == ("unknown", 1, None, "stale_submit_claim_requires_reconciliation")
        provider = FakeProvider()
        replay = worker.dispatch(attempt_id=attempt_id, storage=FakeStorage(), provider=provider)
        assert replay["external_calls"] == 0 and provider.submits == 0
        assert scheduler._pending(dsn, "test-account") == ([], [])
        assert scheduler._unresolved_claims(dsn, "test-account") == ([attempt_id], 1)
        with pytest.raises(RuntimeError, match=f"reconcile:{attempt_id}:unknown_no_task_ref"):
            scheduler._run_batch(dsn, "test-account", SimpleNamespace(
                run_script=lambda **kwargs: pytest.fail("unknown claim was replayed"),
            ))


def test_project_las_worker_and_batch_reject_untrusted_runtime_identity(monkeypatch) -> None:
    def load(name: str):
        spec = util.spec_from_file_location(f"las_test_{name}", LAS / f"{name}.py")
        assert spec and spec.loader
        module = util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    variables = {
        "f/content_research/las_video_understanding_api_key": "not-a-real-key",
        "f/content_research/project_las_worker_email": "trusted@example.com",
        "f/content_research/project_las_account_scope": "test-account",
        "f/content_research/media_storage_config": "{}",
    }
    sensitive_reads: list[str] = []

    def get_variable(path: str):
        if path != "f/content_research/project_las_worker_email":
            sensitive_reads.append(path)
        return variables[path]

    def get_resource(path: str):
        sensitive_reads.append(path)
        return {"host": "127.0.0.1", "user": "unused",
                "password": "unused", "dbname": "unused"}

    monkeypatch.setitem(sys.modules, "wmill", SimpleNamespace(
        get_variable=get_variable,
        get_resource=get_resource,
    ))
    monkeypatch.setenv("WM_EMAIL", "outsider@example.com")
    monkeypatch.delenv("WM_END_USER_EMAIL", raising=False)
    monkeypatch.delenv("WM_SCHEDULE_PATH", raising=False)
    with pytest.raises(RuntimeError, match="worker configuration unavailable"):
        load("dispatch_video")._configuration()
    assert sensitive_reads == []
    with pytest.raises(RuntimeError, match="poll configuration unavailable"):
        load("poll_video").main("11111111-1111-4111-8111-111111111111")
    assert sensitive_reads == []
    with pytest.raises(PermissionError, match="schedule-only"):
        load("run_pending").main()
    monkeypatch.setenv("WM_SCHEDULE_PATH", "f/content_research/project_las/run_pending")
    with pytest.raises(PermissionError, match="schedule identity mismatch"):
        load("run_pending").main()
    # A publisher's trusted WM_EMAIL must not mask an untrusted app viewer.
    monkeypatch.setenv("WM_EMAIL", "trusted@example.com")
    monkeypatch.setenv("WM_END_USER_EMAIL", "outsider@example.com")
    with pytest.raises(RuntimeError, match="worker configuration unavailable"):
        load("dispatch_video")._configuration()
    assert sensitive_reads == []
    with pytest.raises(RuntimeError, match="poll configuration unavailable"):
        load("poll_video").main("11111111-1111-4111-8111-111111111111")
    assert sensitive_reads == []
    with pytest.raises(PermissionError, match="schedule identity mismatch"):
        load("run_pending").main()


def test_project_las_status_and_revoke_survive_media_configuration_outage(monkeypatch) -> None:
    spec = util.spec_from_file_location("las_review_readback_test", BACKEND)
    assert spec and spec.loader
    endpoint = util.module_from_spec(spec)
    spec.loader.exec_module(endpoint)
    sensitive_reads: list[str] = []

    def unavailable_variable(path: str):
        sensitive_reads.append(path)
        raise RuntimeError("media configuration unavailable")

    monkeypatch.setitem(sys.modules, "wmill", SimpleNamespace(get_variable=unavailable_variable))

    class FakeService:
        def __init__(self, dsn, *, delivery_origin, account_scope):
            assert delivery_origin == "https://review-no-media.invalid"
            assert account_scope is None

        def status(self, *, project_id, video_id):
            return {"runs": [], "external_calls": 0}

        def revoke(self, *, project_id, review_id):
            return {"status": "revoked", "external_calls": 0}

    endpoint.ProjectLASService = FakeService
    db = {"host": "127.0.0.1", "user": "unused", "password": "unused", "dbname": "unused"}
    project = "11111111-1111-4111-8111-111111111111"
    video = "22222222-2222-4222-8222-222222222222"
    review = "33333333-3333-4333-8333-333333333333"
    assert endpoint.main(db, project, video, "status") == {"runs": [], "external_calls": 0}
    assert endpoint.main(db, project, video, "revoke", review_id=review) == {
        "status": "revoked", "external_calls": 0,
    }
    assert sensitive_reads == []


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL is required")
def test_project_las_batch_session_lock_skips_overlap_and_releases() -> None:
    spec = util.spec_from_file_location("las_batch_lock_test", LAS / "run_pending.py")
    assert spec and spec.loader
    scheduler = util.module_from_spec(spec)
    spec.loader.exec_module(scheduler)
    dsn = os.environ["TEST_DATABASE_URL"]
    scope = f"test-{uuid4().hex}"
    with scheduler._single_runner(dsn, scope) as first:
        assert first is True
        with scheduler._single_runner(dsn, scope) as overlap:
            assert overlap is False
        with scheduler._single_runner(dsn, scope + "-other") as separate_account:
            assert separate_account is True
    with scheduler._single_runner(dsn, scope) as after_release:
        assert after_release is True
    with pytest.raises(RuntimeError, match="child failed"):
        with scheduler._single_runner(dsn, scope) as before_error:
            assert before_error is True
            raise RuntimeError("child failed")
    with scheduler._single_runner(dsn, scope) as after_error:
        assert after_error is True


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL is required")
def test_project_las_scheduler_selects_expired_task_once_without_repoll(monkeypatch) -> None:
    from tests.test_project_las_live_service import (
        FakeProvider, FakeStorage, _approve_and_prepare, _database, _services,
    )

    monkeypatch.setenv("WM_END_USER_EMAIL", "owner@example.com")
    spec = util.spec_from_file_location("las_run_pending_db_test", LAS / "run_pending.py")
    assert spec and spec.loader
    scheduler = util.module_from_spec(spec)
    spec.loader.exec_module(scheduler)
    with _database() as (dsn, conn, project, video, asset):
        human, worker = _services(dsn)
        _, prepared = _approve_and_prepare(human, project, video, asset, "las-video-v2")
        worker.dispatch(attempt_id=prepared["attempt_id"], storage=FakeStorage(),
                        provider=FakeProvider())
        worker._mark_unknown(prepared["attempt_id"], "poll_outcome_unknown")
        conn.execute("alter table project_las_analysis_attempt disable trigger trg_project_las_provider_task_time")
        try:
            conn.execute(
                """update project_las_analysis_attempt
                      set provider_task_recorded_at=now()-interval '71 hours'
                    where id=%s""", (prepared["attempt_id"],),
            )
        finally:
            conn.execute("alter table project_las_analysis_attempt enable trigger trg_project_las_provider_task_time")
        assert scheduler._pending(dsn, "test-account")[1] == [prepared["attempt_id"]]
        provider = FakeProvider()
        assert worker.poll(attempt_id=prepared["attempt_id"], provider=provider)[
            "external_calls"] == 0
        assert provider.polls == 0
        assert scheduler._pending(dsn, "test-account")[1] == []
