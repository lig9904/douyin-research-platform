import importlib.util
import inspect
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import psycopg

ROOT = Path(__file__).parents[1] / "windmill/f/content_research/analysis"


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


poller = load("poll_pending_asr")
worker = load("run_reviewed_asr")


def test_scheduler_has_no_public_arguments():
    assert not inspect.signature(poller.main).parameters


@pytest.mark.parametrize("saved", [None, ("different-task", "running"), ("matching-task", "submitting"), ("matching-task", "failed")])
def test_resume_guard_never_creates_new_budget_or_calls_provider(monkeypatch, saved):
    monkeypatch.setattr(worker, "_configuration", lambda: ("test", dict(api_key="secret", max_polls=1),
        dict(public_endpoint="https://media.example.test", storage_location="private"), object()))
    monkeypatch.setattr(worker, "request_from_reviewed_asset", lambda *args, **kwargs: SimpleNamespace(source_fingerprint="a" * 64))
    monkeypatch.setattr(worker, "live_asr_task_key", lambda *args: "matching-task")
    class Conn:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql, params):
            assert "where id=%s" in sql
            return SimpleNamespace(fetchone=lambda: saved)
    monkeypatch.setattr(worker.psycopg, "connect", lambda _: Conn())
    monkeypatch.setattr(worker, "LiveASRService", lambda _: pytest.fail("provider"))
    with pytest.raises(RuntimeError, match="inspect persisted"):
        worker.main(str(uuid4()), str(uuid4()), "v1", str(uuid4()))


@pytest.mark.parametrize("statuses", [[], ["completed", "running", "submitted"], ["failed", "completed"], ["exception", "completed"], ["poll_failed", "completed"]])
def test_polling_serializes_saved_job_arguments_and_continues_after_failure(monkeypatch, statuses):
    rows = [(uuid4(), uuid4(), dict(reviewed_asset_id=str(uuid4()), media_review_version="v1")) for _ in statuses]
    monkeypatch.setattr(poller, "_pending", lambda _: rows)
    calls = []
    def run_script(**kwargs):
        index = len(calls)
        calls.append(kwargs)
        job, video, metadata = rows[index]
        assert kwargs == dict(path=poller.WORKER_PATH, args=dict(video_id=str(video),
            asset_id=metadata["reviewed_asset_id"], review_version="v1", resume_job_id=str(job)),
            timeout=310, verbose=False)
        if statuses[index] == "exception":
            raise RuntimeError("private-provider-detail")
        return {"status": "running", "error_code": "poll_failed"} if statuses[index] == "poll_failed" else {"status": statuses[index]}
    def resource(path):
        assert path == "f/content_research/research_db"
        return dict(host="localhost", user="test", password="secret", dbname="test")
    monkeypatch.setitem(sys.modules, "wmill", SimpleNamespace(get_resource=resource, run_script=run_script))
    if any(s in {"failed", "exception", "poll_failed"} for s in statuses):
        with pytest.raises(RuntimeError) as caught:
            poller.main()
        assert "private-provider" not in str(caught.value)
        assert "completed=1" in str(caught.value)
    else:
        assert poller.main() == dict(selected=len(rows), completed=statuses.count("completed"),
            pending=len(statuses)-statuses.count("completed"), failed=0)
    assert len(calls) == len(rows)


def test_invalid_saved_metadata_never_dispatches_worker(monkeypatch):
    monkeypatch.setattr(poller, "_pending", lambda _: [(uuid4(), uuid4(), {})])
    monkeypatch.setitem(sys.modules, "wmill", SimpleNamespace(
        get_resource=lambda _: dict(host="localhost", user="test", password="secret", dbname="test"),
        run_script=lambda **kwargs: pytest.fail("invalid metadata dispatched")))
    with pytest.raises(RuntimeError, match="failed=1"):
        poller.main()


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="isolated PostgreSQL required")
def test_database_selector_excludes_unsubmitted_and_terminal_jobs():
    dsn = os.environ["TEST_DATABASE_URL"]
    video = uuid4()
    cases = [("submitted", 1, "task", "live-asr:", True),
        ("running", 1, "task", "live-asr:", True),
        ("submitting", 0, None, "live-asr:", False),
        ("completed", 1, "task", "live-asr:", False),
        ("failed", 1, "task", "live-asr:", False),
        ("submitted", 1, None, "live-asr:", False),
        ("submitted", 1, "task", "manual:", False)]
    expected = set()
    try:
        with psycopg.connect(dsn) as conn:
            conn.execute("insert into source_video(id,platform,platform_video_id) values (%s,'douyin',%s)", (video, str(video)))
            for status, count, ref, prefix, included in cases:
                identifier = uuid4()
                if included: expected.add(identifier)
                conn.execute("""insert into asr_execution_job(id,task_key,video_id,provider,model_id,
                    model_revision,engine_version,source_fingerprint,media_ref_fingerprint,status,
                    provider_task_ref,submission_count,cost_currency,budget_date,budget_key,updated_at)
                    values (%s,%s,%s,'volcengine-doubao-asr','test','test','test','test','test',
                    %s,%s,%s,'CNY',current_date,'isolated-poll-test','2000-01-01')""",
                    (identifier, prefix+str(identifier), video, status, ref, count))
        selected = {row[0] for row in poller._pending(dsn) if row[1] == video}
        assert selected == expected
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute("delete from source_video where id=%s", (video,))
