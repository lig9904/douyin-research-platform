import importlib.util
import inspect
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from datetime import date

import pytest
from douyin_research.l2.live_asr_service import LiveASRRequest

PATH = Path(__file__).parents[1] / "windmill/f/content_research/analysis/run_reviewed_asr.py"
SPEC = importlib.util.spec_from_file_location("live_asr_worker", PATH)
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


def test_public_inputs_cannot_supply_url_key_or_actor():
    assert list(inspect.signature(worker.main).parameters) == ["video_id", "asset_id", "review_version"]


def test_invalid_id_stops_before_configuration(monkeypatch):
    monkeypatch.setattr(worker, "_configuration", lambda: pytest.fail("secret read"))
    with pytest.raises(ValueError):
        worker.main("invalid", str(uuid4()), "v1")


@pytest.mark.parametrize("status", ["submitted", "running", "completed", "failed", "reconciliation_required"])
def test_worker_preserves_prior_date_and_reports_failure(monkeypatch, status):
    video, asset = uuid4(), uuid4()
    cfg = dict(api_key="secret", max_polls=1, max_daily_requests=None, max_daily_cost_cny=None)
    monkeypatch.setattr(worker, "_configuration", lambda: ("test", cfg,
        dict(public_endpoint="https://media.example.test", storage_location="private"), object()))
    request = LiveASRRequest(dsn="test", video_id=video, media_url="https://media.example.test/object",
        media_review_version="v1", media_query_sha256=None, audio_format="wav",
        source_fingerprint="a" * 64, api_key="secret", reviewed_asset_id=asset)
    monkeypatch.setattr(worker, "request_from_reviewed_asset", lambda *args, **kwargs: request)
    prior_day = date(2026, 9, 19)
    writes = []
    class Conn:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql, params):
            if sql.startswith("insert"):
                writes.append(params)
            return SimpleNamespace(fetchone=lambda: (prior_day,))
    monkeypatch.setattr(worker.psycopg, "connect", lambda _: Conn())
    def run(req):
        assert req.budget_date == prior_day
        return {"status": status}
    monkeypatch.setattr(worker, "LiveASRService", lambda _: SimpleNamespace(run=run))
    if status in {"submitted", "running", "completed"}:
        assert worker.main(str(video), str(asset), "v1") == {"status": status}
    else:
        with pytest.raises(RuntimeError, match="inspect persisted"):
            worker.main(str(video), str(asset), "v1")
    assert writes == []  # Never recreate or reset accounting for an existing job.


def test_unapproved_media_cannot_reach_budget_or_provider(monkeypatch):
    monkeypatch.setattr(worker, "_configuration", lambda: ("test", dict(api_key="secret", max_polls=1),
        dict(public_endpoint="https://media.example.test", storage_location="private"), object()))
    def reject(*args, **kwargs): raise ValueError("private-unapproved-detail")
    monkeypatch.setattr(worker, "request_from_reviewed_asset", reject)
    monkeypatch.setattr(worker.psycopg, "connect", lambda _: pytest.fail("budget write"))
    with pytest.raises(RuntimeError) as caught:
        worker.main(str(uuid4()), str(uuid4()), "v1")
    assert "private-unapproved" not in str(caught.value)


@pytest.mark.parametrize("override", [
    {"api_key": ""}, {"max_polls": True}, {"max_polls": 4},
    {"max_daily_requests": -1}, {"max_daily_requests": True},
    {"max_daily_cost_cny": -1}, {"max_daily_cost_cny": float("inf")},
    {"max_daily_cost_cny": True}, {"unexpected": "secret-detail"},
])
def test_configuration_rejects_invalid_policy_without_storage(monkeypatch, override):
    cfg = dict(api_key="private-secret", max_polls=1,
        max_daily_requests=None, max_daily_cost_cny=None)
    cfg.update(override)
    monkeypatch.setitem(sys.modules, "wmill", SimpleNamespace(
        get_resource=lambda path: dict(host="localhost", user="test", password="private-password", dbname="test"),
        get_variable=lambda path: json.dumps(cfg)))
    monkeypatch.setattr(worker, "PrivateS3MediaStorage", lambda *args: pytest.fail("storage constructed"))
    with pytest.raises(RuntimeError) as caught:
        worker._configuration()
    assert str(caught.value) == "ASR worker configuration unavailable"
    assert caught.value.__suppress_context__


def test_configuration_uses_fixed_paths_and_accepts_unlimited_policy(monkeypatch):
    calls = []
    cfg = dict(api_key="private-secret", max_polls=1,
        max_daily_requests=None, max_daily_cost_cny=None)
    media = dict(endpoint="http://127.0.0.1:9000", public_endpoint="https://media.example.test",
        bucket="research-media", region="us-east-1", access_key_id="test-access",
        secret_access_key="test-secret", force_path_style=True, use_ssl=False,
        storage_location="private")
    def resource(path):
        calls.append(path)
        return dict(host="localhost", user="test", password="private-password", dbname="test")
    def variable(path):
        calls.append(path)
        return json.dumps(cfg if path.endswith("asr_worker_config") else media)
    monkeypatch.setitem(sys.modules, "wmill", SimpleNamespace(get_resource=resource, get_variable=variable))
    sentinel = object()
    monkeypatch.setattr(worker, "PrivateS3MediaStorage", lambda config: sentinel)
    _, actual, _, storage = worker._configuration()
    assert actual == cfg and storage is sentinel
    assert calls == ["f/content_research/research_db", "f/content_research/asr_worker_config",
        "f/content_research/media_storage_config"]


def test_existing_job_missing_budget_fails_without_reset_or_provider(monkeypatch):
    video, asset = uuid4(), uuid4()
    monkeypatch.setattr(worker, "_configuration", lambda: ("test", dict(api_key="secret", max_polls=1),
        dict(public_endpoint="https://media.example.test", storage_location="private"), object()))
    monkeypatch.setattr(worker, "request_from_reviewed_asset", lambda *args, **kwargs:
        SimpleNamespace(source_fingerprint="a" * 64))
    class Conn:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql, params):
            assert not sql.startswith("insert")
            return SimpleNamespace(fetchone=lambda: (date(2026, 9, 19),)
                if "asr_execution_job" in sql else None)
    monkeypatch.setattr(worker.psycopg, "connect", lambda _: Conn())
    monkeypatch.setattr(worker, "LiveASRService", lambda _: pytest.fail("provider reached"))
    with pytest.raises(RuntimeError, match="inspect persisted"):
        worker.main(str(video), str(asset), "v1")
