import importlib.util
import inspect
import json
import os
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import psycopg
from psycopg.types.json import Jsonb

PATH = Path(__file__).parents[1] / "windmill/f/content_research/analysis/run_reviewed_l3.py"
SPEC = importlib.util.spec_from_file_location("live_l3_worker", PATH)
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


def selection():
    return worker.ReviewedL3Selection(video_id=uuid4(), privacy_review_version="v1",
        expected_input_fingerprint="a" * 64, expected_evidence_version="evidence-v1",
        prompt_version="prompt-v1", estimated_llm_cost=None, confirmation=worker.L3_CONFIRMATION)


def configuration():
    return dict(ark=dict(api_key="private-key", endpoint_id="ep-test", model_revision="test-revision",
        expected_response_model="ep-test", pricing_version="test-price",
        input_cost_per_million_tokens=1, output_cost_per_million_tokens=2),
        prompt_version="prompt-v1", max_daily_requests=None, max_daily_cost_cny=None)


def test_only_persisted_approval_is_a_public_input(monkeypatch):
    assert list(inspect.signature(worker.main).parameters) == ["approval_id"]
    monkeypatch.setattr(worker, "_configuration", lambda: pytest.fail("secret read"))
    with pytest.raises(ValueError):
        worker.main("invalid")


@pytest.mark.parametrize("status", ["completed", "failed", "running", "blocked_missing_or_stale_l3_evidence", "reconciliation_required"])
def test_worker_runs_fixed_selection_and_fails_noncompleted(monkeypatch, status):
    cfg = configuration()
    ark = worker.LiveArkConfiguration(**cfg["ark"])
    selected = selection()
    prior_day = date(2026, 9, 19)
    monkeypatch.setattr(worker, "_configuration", lambda: ("test", cfg, ark))
    monkeypatch.setattr(worker, "_selection", lambda *args: selected)
    monkeypatch.setattr(worker, "_reservation_date", lambda *args: prior_day)
    def execute(dsn, *, selection, ark):
        assert dsn == "test" and selection.budget_date == prior_day
        assert selection.expected_input_fingerprint == selected.expected_input_fingerprint
        assert selection.estimated_llm_cost is None
        return {"status": status}
    monkeypatch.setattr(worker, "execute_reviewed_live_ark", execute)
    if status == "completed":
        assert worker.main(str(uuid4())) == {"status": "completed"}
    else:
        with pytest.raises(RuntimeError, match="inspect persisted"):
            worker.main(str(uuid4()))


def test_missing_approval_never_initializes_budget_or_executes(monkeypatch):
    cfg = configuration()
    monkeypatch.setattr(worker, "_configuration", lambda: ("test", cfg, object()))
    def reject(*args): raise ValueError("private-detail")
    monkeypatch.setattr(worker, "_selection", reject)
    monkeypatch.setattr(worker, "_reservation_date", lambda *args: pytest.fail("budget write"))
    monkeypatch.setattr(worker, "execute_reviewed_live_ark", lambda *args, **kwargs: pytest.fail("execution"))
    with pytest.raises(RuntimeError) as caught:
        worker.main(str(uuid4()))
    assert "private-detail" not in str(caught.value)


@pytest.mark.parametrize("prior,budget", [(True, True), (True, False), (False, False)])
def test_budget_recovery_never_recreates_old_accounting(monkeypatch, prior, budget):
    calls = []
    old_day = date(2026, 9, 19)
    class Conn:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql, params):
            calls.append((sql, params))
            result = ((old_day,) if prior else None) if "l3_execution_job" in sql else ((1,) if budget else None)
            return SimpleNamespace(fetchone=lambda: result)
    monkeypatch.setattr(worker.psycopg, "connect", lambda _: Conn())
    cfg = configuration()
    args = ("test", selection(), worker.LiveArkConfiguration(**cfg["ark"]), cfg)
    if prior and not budget:
        with pytest.raises(RuntimeError, match="reconciliation"):
            worker._reservation_date(*args)
    else:
        assert worker._reservation_date(*args) == (old_day if prior else date.today())
    inserts = [params for sql, params in calls if sql.startswith("insert")]
    assert inserts == ([] if prior else [(date.today(), worker.VOLCENGINE_ARK_L3_PROVIDER, worker.L3_BUDGET_KEY, None, None)])


@pytest.mark.parametrize("override", [
    {"prompt_version": ""}, {"max_daily_requests": True}, {"max_daily_requests": -1},
    {"max_daily_cost_cny": float("nan")}, {"max_daily_cost_cny": True},
    {"max_daily_cost_cny": -1}, {"caller_actor": "admin"},
])
def test_invalid_fixed_configuration_is_redacted(monkeypatch, override):
    cfg = configuration()
    cfg.update(override)
    monkeypatch.setitem(sys.modules, "wmill", SimpleNamespace(
        get_resource=lambda _: dict(host="localhost", user="test", password="private-password", dbname="test"),
        get_variable=lambda _: json.dumps(cfg)))
    with pytest.raises(RuntimeError) as caught:
        worker._configuration()
    assert str(caught.value) == "L3 worker configuration unavailable"


def test_fixed_configuration_accepts_explicit_unlimited_policy(monkeypatch):
    cfg = configuration()
    calls = []
    def resource(path):
        calls.append(path)
        return dict(host="localhost", user="test", password="private-password", dbname="test")
    def variable(path):
        calls.append(path)
        return json.dumps(cfg)
    monkeypatch.setitem(sys.modules, "wmill", SimpleNamespace(get_resource=resource, get_variable=variable))
    _, actual, ark = worker._configuration()
    assert actual == cfg and ark.api_key == "private-key"
    assert calls == ["f/content_research/research_db", "f/content_research/l3_worker_config"]


@pytest.mark.parametrize("field", ["input_cost_per_million_tokens", "output_cost_per_million_tokens"])
def test_zero_price_rejected_before_approval_or_budget(monkeypatch, field):
    cfg = configuration()
    cfg["ark"][field] = 0
    monkeypatch.setitem(sys.modules, "wmill", SimpleNamespace(
        get_resource=lambda _: dict(host="localhost", user="test", password="secret", dbname="test"),
        get_variable=lambda _: json.dumps(cfg)))
    monkeypatch.setattr(worker, "_selection", lambda *args: pytest.fail("approval queried"))
    monkeypatch.setattr(worker, "_reservation_date", lambda *args: pytest.fail("budget created"))
    with pytest.raises(RuntimeError, match="inspect persisted"):
        worker.main(str(uuid4()))


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="isolated PostgreSQL required")
def test_persisted_selection_rejects_superseded_and_revoked_approval():
    dsn = os.environ["TEST_DATABASE_URL"]
    video, approved_id, revoked_id = uuid4(), uuid4(), uuid4()
    value = dict(reviewed=True, version="privacy-v1", evidence_fingerprint="a" * 64,
        evidence_version="evidence-v1", evidence_modalities=["metadata"],
        idempotency_key=str(uuid4()), reviewer_identity_source=worker.L3_REVIEW_IDENTITY_SOURCE)
    try:
        with psycopg.connect(dsn) as conn:
            conn.execute("insert into source_video(id,platform,platform_video_id) values (%s,'douyin',%s)",
                (video, str(video)))
            conn.execute("""insert into human_annotation(id,video_id,actor,annotation_type,value,created_at)
                values (%s,%s,'isolated-test','l3_privacy_review',%s,now()-interval '1 second')""",
                (approved_id, video, Jsonb(value)))
        result = worker._selection(dsn, approved_id, "prompt-v1")
        assert result.video_id == video and result.expected_input_fingerprint == "a" * 64
        assert result.estimated_llm_cost is None
        with psycopg.connect(dsn) as conn:
            conn.execute("""insert into human_annotation(id,video_id,actor,annotation_type,value)
                values (%s,%s,'isolated-test','l3_privacy_review',%s)""",
                (revoked_id, video, Jsonb(dict(reviewed=False))))
        for identifier in (approved_id, revoked_id, uuid4()):
            with pytest.raises(ValueError, match="unavailable"):
                worker._selection(dsn, identifier, "prompt-v1")
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute("delete from source_video where id=%s", (video,))


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="isolated PostgreSQL required")
def test_real_budget_creation_preservation_and_missing_prior_row(monkeypatch):
    dsn = os.environ["TEST_DATABASE_URL"]
    cfg = configuration()
    ark = worker.LiveArkConfiguration(**cfg["ark"])
    selected = selection()
    key = "isolated-l3-worker-" + str(uuid4())
    monkeypatch.setattr(worker, "L3_BUDGET_KEY", key)
    task_key = worker.live_ark_task_key(selected.video_id, selected.expected_input_fingerprint,
        model_id=ark.model_id, model_revision=ark.model_revision,
        prompt_version=selected.prompt_version, schema_version=worker.L3_SCHEMA_VERSION)
    old_day = date(2026, 9, 19)
    try:
        assert worker._reservation_date(dsn, selected, ark, cfg) == date.today()
        with psycopg.connect(dsn) as conn:
            conn.execute("update daily_budget set used_requests=2,spent_cost=3 where budget_key=%s", (key,))
        worker._reservation_date(dsn, selected, ark, cfg)
        with psycopg.connect(dsn) as conn:
            assert conn.execute("select used_requests,spent_cost,max_cost from daily_budget where budget_key=%s",
                (key,)).fetchone() == (2, 3, None)
            conn.execute("insert into source_video(id,platform,platform_video_id) values (%s,'douyin',%s)",
                (selected.video_id, str(selected.video_id)))
            conn.execute("""insert into l3_execution_job(id,task_key,video_id,provider,model_id,model_revision,
                prompt_version,schema_version,input_fingerprint,status,cost_currency,budget_date,budget_key)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,'running','CNY',%s,%s)""",
                (uuid4(), task_key, selected.video_id, worker.VOLCENGINE_ARK_L3_PROVIDER,
                 ark.model_id, ark.model_revision, selected.prompt_version, worker.L3_SCHEMA_VERSION,
                 selected.expected_input_fingerprint, old_day, key))
        with pytest.raises(RuntimeError, match="reconciliation"):
            worker._reservation_date(dsn, selected, ark, cfg)
        with psycopg.connect(dsn) as conn:
            assert conn.execute("select count(*) from daily_budget where budget_key=%s", (key,)).fetchone() == (1,)
            conn.execute("""insert into daily_budget(budget_date,provider,budget_key,cost_currency)
                values (%s,%s,%s,'CNY')""", (old_day, worker.VOLCENGINE_ARK_L3_PROVIDER, key))
        assert worker._reservation_date(dsn, selected, ark, cfg) == old_day
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute("delete from source_video where id=%s", (selected.video_id,))
            conn.execute("delete from daily_budget where budget_key=%s", (key,))
