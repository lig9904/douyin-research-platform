from contextlib import contextmanager, nullcontext
import json
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

from douyin_research import reviewed_dispatch as dispatch


@pytest.fixture
def harness(monkeypatch):
    state = SimpleNamespace(rows=[], calls=[], closed=False, existing={}, rejected=set())

    @contextmanager
    def candidates(dsn, stage):
        try:
            yield iter(state.rows)
        finally:
            state.closed = True

    monkeypatch.setattr(dispatch, "_candidates", candidates)
    monkeypatch.setattr(dispatch, "live_asr_task_key", lambda video, fingerprint: fingerprint)
    monkeypatch.setattr(dispatch, "_existing", lambda dsn, stage, key, video, fingerprint: state.existing.get(key))
    monkeypatch.setattr(dispatch, "MediaAssetStore", lambda dsn: SimpleNamespace(
        get=lambda video, asset: SimpleNamespace(bucket="private", object_key="audio.wav")))

    def review(dsn, **kwargs):
        if kwargs["source_fingerprint"] in state.rejected:
            raise ValueError("stale review")

    monkeypatch.setattr(dispatch, "assert_reviewed_delivery", review)

    def execute(path, args):
        state.calls.append((path, args))
        return {"status": "submitted"}

    state.execute = execute
    return state


def row(index):
    return (uuid4(), uuid4(), "review-v1", str(index))


def run(state, **kwargs):
    return dispatch.dispatch_reviewed("unused", stage="asr", execute_worker=state.execute,
                                      public_origin="https://storage.example:6443", **kwargs)


def test_completed_history_does_not_starve_new_approval(harness):
    harness.rows = [row(i) for i in range(1201)]
    harness.existing = {str(i): "existing" for i in range(1200)}
    result = run(harness)
    assert result["existing"] == 1200
    assert result["dispatched"] == 1
    assert harness.closed
    video, asset, version, _ = harness.rows[-1]
    assert harness.calls == [(dispatch.WORKERS["asr"], {
        "video_id": str(video), "asset_id": str(asset), "review_version": version})]


def test_stale_approvals_do_not_consume_submission_slots(harness):
    harness.rows = [row(i) for i in range(12)]
    harness.rejected = {str(i) for i in range(7)}
    result = run(harness)
    assert result["blocked"] == 7
    assert result["dispatched"] == 5


def test_existing_and_uncertain_jobs_never_dispatch(harness):
    harness.rows = [row(0), row(1)]
    harness.existing = {"0": "existing", "1": "attention"}
    result = run(harness)
    assert result["existing"] == result["attention"] == 1
    assert harness.calls == []


def test_batch_is_bounded_and_cursor_closed(harness):
    harness.rows = [row(i) for i in range(20)]
    assert run(harness)["dispatched"] == 5
    assert len(harness.calls) == 5
    assert harness.closed


def test_next_tick_skips_persisted_submissions(harness):
    harness.rows = [row(i) for i in range(3)]
    first_execute = harness.execute

    def persist(path, args):
        result = first_execute(path, args)
        fingerprint = next(r[3] for r in harness.rows if str(r[1]) == args["asset_id"])
        harness.existing[fingerprint] = "existing"
        return result

    harness.execute = persist
    assert run(harness)["dispatched"] == 3
    assert run(harness)["existing"] == 3
    assert len(harness.calls) == 3


@pytest.mark.parametrize("result", [None, {}, {"status": "failed"},
                                   {"status": "running", "error_code": "needs_reconciliation"}])
def test_invalid_or_uncertain_worker_result_is_not_success(harness, result):
    harness.rows = [row(0)]
    harness.execute = lambda path, args: result
    counts = run(harness)
    assert counts["failed"] == 1
    assert counts["dispatched"] == 0


def test_worker_errors_are_redacted_and_bounded(harness):
    harness.rows = [row(i) for i in range(20)]

    def fail(path, args):
        raise RuntimeError("secret-url-and-provider-reference")

    harness.execute = fail
    result = run(harness)
    assert result["failed"] == 5
    assert result["dispatched"] == 0
    assert "secret" not in str(result)
    assert harness.closed


@pytest.mark.parametrize("origin", [None, "", "http://storage.example", "https://u:p@storage.example",
                                  "https://storage.example/prefix", "https://storage.example?key=x"])
def test_invalid_origin_fails_before_selection(harness, origin):
    with pytest.raises(ValueError, match="HTTPS public origin"):
        dispatch.dispatch_reviewed("unused", stage="asr", execute_worker=harness.execute,
                                   public_origin=origin)
    assert not harness.closed
    assert harness.calls == []


def test_scheduled_asr_uses_fixed_resources_and_quiet_child(monkeypatch):
    monkeypatch.setattr(dispatch, "scheduler_slot", lambda dsn: nullcontext(True))
    calls = []
    def resource(path):
        assert path == "f/content_research/research_db"
        return dict(host="localhost", user="test", password="private", dbname="synthetic")
    def variable(path):
        assert path == "f/content_research/media_storage_config"
        return json.dumps({"public_endpoint": "https://media.example"})
    def child(**kwargs):
        calls.append(kwargs)
        return {"status": "submitted"}
    monkeypatch.setitem(sys.modules, "wmill", SimpleNamespace(
        get_resource=resource, get_variable=variable, run_script=child))
    def select(dsn, **kwargs):
        assert kwargs["stage"] == "asr"
        assert kwargs["public_origin"] == "https://media.example"
        kwargs["execute_worker"](dispatch.WORKERS["asr"], {"asset_id": "synthetic"})
        return dict(dispatched=1, failed=0, attention=0, blocked=0)
    monkeypatch.setattr(dispatch, "dispatch_reviewed", select)
    assert dispatch.run_scheduled("asr")["dispatched"] == 1
    assert calls == [dict(path=dispatch.WORKERS["asr"], args={"asset_id": "synthetic"},
                          timeout=310, verbose=False)]


def test_scheduled_configuration_errors_do_not_expose_secrets(monkeypatch):
    def fail(path):
        raise RuntimeError("private-password-or-url")
    monkeypatch.setitem(sys.modules, "wmill", SimpleNamespace(get_resource=fail))
    with pytest.raises(RuntimeError) as error:
        dispatch.run_scheduled("asr")
    assert "private-password" not in str(error.value)
    assert error.value.__suppress_context__


def test_busy_parent_returns_without_selecting_or_waiting(monkeypatch):
    monkeypatch.setitem(sys.modules, "wmill", SimpleNamespace(
        get_resource=lambda path: dict(host="localhost", user="test", password="synthetic", dbname="test"),
        get_variable=lambda path: json.dumps({"public_endpoint": "https://media.example"})))
    monkeypatch.setattr(dispatch, "scheduler_slot", lambda dsn: nullcontext(False))
    monkeypatch.setattr(dispatch, "dispatch_reviewed", lambda *args, **kwargs: pytest.fail("busy selection"))
    assert dispatch.run_scheduled("asr") == dict(status="deferred", reason="analysis_scheduler_busy")


def test_scheduled_l3_wait_exceeds_child_timeout(monkeypatch):
    monkeypatch.setattr(dispatch, "scheduler_slot", lambda dsn: nullcontext(True))
    calls = []
    cfg = dict(ark=dict(api_key="synthetic", endpoint_id="ep-test", model_revision="r1",
        expected_response_model="ep-test", pricing_version="test",
        input_cost_per_million_tokens=1, output_cost_per_million_tokens=2),
        prompt_version="p1", max_daily_requests=None, max_daily_cost_cny=None)
    monkeypatch.setitem(sys.modules, "wmill", SimpleNamespace(
        get_resource=lambda path: dict(host="localhost", user="test", password="synthetic", dbname="test"),
        get_variable=lambda path: json.dumps(cfg), run_script=lambda **kwargs: calls.append(kwargs)))
    def select(dsn, **kwargs):
        assert kwargs["stage"] == "l3"
        assert kwargs["prompt_version"] == "p1"
        kwargs["execute_worker"](dispatch.WORKERS["l3"], {"approval_id": "synthetic"})
        return dict(dispatched=1, failed=0, attention=0, blocked=0)
    monkeypatch.setattr(dispatch, "dispatch_reviewed", select)
    dispatch.run_scheduled("l3")
    assert calls[0]["timeout"] == 370
    assert calls[0]["verbose"] is False


@pytest.mark.parametrize("stale", [False, True])
def test_l3_current_evidence_only_passes_approval_id(harness, monkeypatch, stale):
    from douyin_research.l3.live_service import LiveArkConfiguration
    ark = LiveArkConfiguration(api_key="synthetic", endpoint_id="ep-test", model_revision="r1",
        expected_response_model="ep-test", pricing_version="synthetic-v1",
        input_cost_per_million_tokens=1, output_cost_per_million_tokens=2)
    video, approval = uuid4(), uuid4()
    harness.rows = [(video, approval, "review-v1", "a" * 64, "evidence-v1")]
    evidence = SimpleNamespace(input_fingerprint=("b" if stale else "a") * 64,
                               evidence_version="evidence-v1")
    monkeypatch.setattr(dispatch, "L3EvidenceAssembler", lambda dsn: SimpleNamespace(
        assemble=lambda *args, **kwargs: evidence))
    def execute(path, args):
        harness.calls.append((path, args))
        return {"status": "completed"}
    counts = dispatch.dispatch_reviewed("unused", stage="l3", ark=ark,
        prompt_version="prompt-v1", execute_worker=execute)
    assert counts["blocked"] == int(stale)
    assert counts["dispatched"] == int(not stale)
    assert harness.calls == ([] if stale else [(dispatch.WORKERS["l3"], {"approval_id": str(approval)})])
