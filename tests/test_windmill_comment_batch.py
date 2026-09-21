import importlib.util
import inspect
import json
import sys
import os
from datetime import date, timedelta
from types import SimpleNamespace
from pathlib import Path
from uuid import uuid4

import pytest
import psycopg

PATH = Path(__file__).parents[1] / "windmill/f/content_research/collectors/process_comment_batch.py"
SPEC = importlib.util.spec_from_file_location("comment_batch_worker", PATH)
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


def test_comment_worker_release_lock_matches_source():
    import re
    lock = PATH.with_suffix('.script.lock').read_text()
    metadata = PATH.with_suffix('.script.yaml').read_text()
    commit = re.search(r'douyin-research-platform@([0-9a-f]{40})', PATH.read_text()).group(1)
    assert f'douyin-research-platform@{commit}' in lock
    assert lock.startswith('# workspace-dependencies-mode: manual\n# py: 3.13\n')
    assert "lock: '!inline f/content_research/collectors/process_comment_batch.script.lock'" in metadata
    assert 'psycopg==3.3.6' in lock
    assert 'wmill==1.815.0' in lock


def test_only_persisted_batch_id_is_public():
    assert list(inspect.signature(worker.main).parameters) == ["source_run_id"]
    with pytest.raises(TypeError):
        worker.main(source_run_id=str(uuid4()), actor="spoof")


def test_invalid_uuid_cannot_load_configuration(monkeypatch):
    monkeypatch.setattr(worker, "_configuration", lambda: pytest.fail("configuration read"))
    with pytest.raises(ValueError):
        worker.main("invalid")


def test_preflight_rejection_precedes_key_lookup(monkeypatch):
    monkeypatch.setattr(worker, "_configuration", lambda: ("unused", "worker", worker.CommentPipelineSettings(top_n=2)))
    def reject(*args):
        raise ValueError("batch unavailable")
    monkeypatch.setattr(worker, "_preflight", reject)
    with pytest.raises(ValueError, match="batch unavailable"):
        worker.main(str(uuid4()))


def test_transport_errors_are_redacted():
    class Raw:
        def call(self, *args):
            raise ValueError("private-token")
    with pytest.raises(RuntimeError) as caught:
        worker._SafeTransport(Raw()).call(None, {})
    assert "private-token" not in str(caught.value)


@pytest.mark.parametrize("extra", [{"actor": "spoof"}, {"top_n": 0}, {"quota_date": "2026-01-01"}])
def test_server_settings_reject_unknown_or_invalid_fields(monkeypatch, extra):
    config = {"top_n": 2, **extra}
    monkeypatch.setitem(sys.modules, "wmill", SimpleNamespace(
        get_resource=lambda path: dict(host="localhost", user="test", password="private-key", dbname="test"),
        get_variable=lambda path: "worker" if path == worker.IDENTITY_PATH else json.dumps(config)))
    with pytest.raises(RuntimeError, match="configuration unavailable") as caught:
        worker._configuration()
    assert "private-key" not in str(caught.value)


@pytest.mark.parametrize("outcome", ["success", "failed"])
def test_execution_uses_server_identity_and_closes_transport(monkeypatch, outcome):
    seen = {}
    monkeypatch.setattr(worker, "_configuration", lambda: ("unused", "fixed/comments", worker.CommentPipelineSettings(top_n=2)))
    monkeypatch.setattr(worker, "_preflight", lambda *args: None)
    monkeypatch.setitem(sys.modules, "wmill", SimpleNamespace(get_variable=lambda path: "private-key"))
    class Transport:
        def __init__(self, key, max_retries):
            assert max_retries == 0
        def close(self):
            seen["closed"] = True
    monkeypatch.setattr(worker, "TikHubTransport", Transport)
    for name in ("PostgresProviderStore", "CommentEvidenceStore", "L0L1Store"):
        monkeypatch.setattr(worker, name, lambda *args: None)
    monkeypatch.setattr(worker, "TikHubProvider", lambda **kwargs: None)
    monkeypatch.setattr(worker, "CommentCollector", lambda **kwargs: None)
    def pipeline(dsn, collector, worker_identity):
        seen["actor"] = worker_identity
        def run(*args, **kwargs):
            seen["new_candidates_only"] = kwargs["settings"].new_candidates_only
            return SimpleNamespace(
                pipeline_run_id=uuid4(),
                videos=[SimpleNamespace(outcome=outcome)],
                promotion=SimpleNamespace(selected_count=1),
            )
        return SimpleNamespace(run=run)
    monkeypatch.setattr(worker, "CommentPipeline", pipeline)
    if outcome == "success":
        result = worker.main(str(uuid4()))
        assert result["selected_count"] == 1
        assert "private-key" not in repr(result)
    else:
        with pytest.raises(RuntimeError, match="inspect persisted"):
            worker.main(str(uuid4()))
    assert seen == {
        "actor": "fixed/comments",
        "new_candidates_only": True,
        "closed": True,
    }


@pytest.mark.parametrize("policy", [{}, {"max_requests": True, "max_cost_usd": None, "max_l3_items": 3},
    {"max_requests": None, "max_cost_usd": -1, "max_l3_items": 3}])
def test_daily_policy_must_be_explicit_and_valid(monkeypatch, policy):
    monkeypatch.setitem(sys.modules, "wmill", SimpleNamespace(get_variable=lambda path: json.dumps(policy)))
    with pytest.raises(RuntimeError, match="explicit daily"):
        worker._daily_policy()


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="isolated PostgreSQL required")
def test_daily_preparation_preserves_existing_limits_and_usage(monkeypatch):
    key = "worker-test-" + str(uuid4())
    monkeypatch.setattr(worker, "BUDGET_KEY", key)
    settings = SimpleNamespace(quota_key=key, quota_date=date.today())
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as conn:
        try:
            worker._prepare_day(conn, settings, dict(max_requests=None, max_cost_usd=None, max_l3_items=5))
            conn.execute("update daily_budget set used_requests=2,max_requests=10 where budget_key=%s", (key,))
            conn.execute("update daily_research_quota set used_items=1 where quota_key=%s", (key,))
            worker._prepare_day(conn, settings, dict(max_requests=99, max_cost_usd=1, max_l3_items=99))
            assert conn.execute("select used_requests,max_requests,max_cost from daily_budget where budget_key=%s", (key,)).fetchone() == (2, 10, None)
            assert conn.execute("select used_items,max_items from daily_research_quota where quota_key=%s", (key,)).fetchone() == (1, 5)
        finally:
            conn.rollback()


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="isolated PostgreSQL required")
def test_preflight_creates_daily_records_only_for_valid_source(monkeypatch):
    dsn = os.environ["TEST_DATABASE_URL"]
    source, video = uuid4(), uuid4()
    key = "preflight-" + str(source)
    settings = SimpleNamespace(quota_key=key, quota_date=date.today())
    monkeypatch.setattr(worker, "BUDGET_KEY", key)
    calls = []
    monkeypatch.setattr(worker, "_daily_policy", lambda: calls.append("policy") or
                        dict(max_requests=None, max_cost_usd=None, max_l3_items=3))
    with pytest.raises(ValueError, match="successful Douyin"):
        worker._preflight(dsn, source, settings)
    assert calls == []
    try:
        with psycopg.connect(dsn) as conn:
            conn.execute("insert into source_video(id,platform,platform_video_id) values (%s,'douyin',%s)", (video, str(video)))
            conn.execute("insert into pipeline_run(id,run_type,run_version,platform,status) values (%s,'l0l1_discovery','test','douyin','success')", (source,))
            conn.execute("insert into pipeline_run_item(run_id,entity_type,entity_id,stage,outcome) values (%s,'video',%s,'L1','scored')", (source, video))
        worker._preflight(dsn, source, settings)
        worker._preflight(dsn, source, settings)
        class NextDay(date):
            @classmethod
            def today(cls):
                return settings.quota_date + timedelta(days=1)
        monkeypatch.setattr("douyin_research.l0l1.budget.date", NextDay)
        monkeypatch.setattr(worker, "date", NextDay)
        worker._budget_hook(dsn, settings.quota_date)(SimpleNamespace(paid=True, unit_cost_usd=0.001))
        with psycopg.connect(dsn) as conn:
            assert conn.execute("select budget_date,max_cost,max_requests,used_requests from daily_budget where budget_key=%s", (key,)).fetchall() == [(settings.quota_date, None, None, 1)]
            assert conn.execute("select max_items,used_items from daily_research_quota where quota_key=%s", (key,)).fetchall() == [(3, 0)]
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute("delete from daily_budget where budget_key=%s", (key,))
            conn.execute("delete from daily_research_quota where quota_key=%s", (key,))
            conn.execute("delete from pipeline_run where id=%s", (source,))
            conn.execute("delete from source_video where id=%s", (video,))
