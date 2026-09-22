from __future__ import annotations

import importlib.util
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).parents[1]
    / "windmill/f/content_research/collectors/scheduled_golden_intake.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("scheduled_golden_intake", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _resource():
    return {"host": "db", "port": 5432, "user": "worker", "password": "private", "dbname": "research", "sslmode": "prefer"}


@contextmanager
def _lock(acquired: bool):
    yield acquired


def _success_result():
    return {
        "run_id": "1f852a3b-6c68-4a90-958e-7330a4c2f6ae",
        "source_count": 2,
        "observations": 3,
        "unique_platform_videos": 2,
        "new_candidate_count": 1,
        "scored_videos": 2,
        "provider_call_count": 2,
        "cached_call_count": 0,
        "uncached_call_count": 2,
    }


def test_invalid_settings_fail_before_key_lookup(monkeypatch) -> None:
    module = load_module()
    calls = []
    monkeypatch.setattr(module, "_database_resource", _resource)
    monkeypatch.setattr(
        module, "_windmill_variable",
        lambda path: calls.append(path) or (
            "scheduled-worker" if path == module.IDENTITY_PATH else "{}"
        ),
    )

    with pytest.raises(RuntimeError, match="configuration is unavailable"):
        module.main()
    assert module.API_KEY_PATH not in calls


def test_main_uses_service_identity_not_end_user_and_returns_aggregate_run(monkeypatch) -> None:
    module = load_module()
    calls = []
    monkeypatch.delenv("WM_END_USER_EMAIL", raising=False)
    monkeypatch.setattr(module, "_database_resource", _resource)
    monkeypatch.setattr(
        module, "_windmill_variable",
        lambda path: calls.append(path) or {
            module.IDENTITY_PATH: "scheduled-worker",
            module.SETTINGS_PATH: '{"max_items":5,"date_window_hours":24}',
            module.API_KEY_PATH: "private-key",
        }[path],
    )
    monkeypatch.setattr(module, "_single_paid_job", lambda _dsn: _lock(True))
    monkeypatch.setattr(module, "_completed_run", lambda dsn, run_id: calls.append(("verified", str(run_id))))

    def run_live(**kwargs):
        calls.append(("run", kwargs))
        assert kwargs["triggered_by"] == "scheduled-worker"
        plan = kwargs["plan"]
        assert plan.enrich_details is True and plan.force_refresh is False
        assert plan.novel_candidates_only is True
        assert plan.max_external_calls == 2 and plan.max_cost_usd is None
        assert plan.detail_strategy == "batch50" and plan.retry_count == 0
        return _success_result()

    monkeypatch.setattr(module, "run_live", run_live)
    result = module.main()

    assert result == {
        "status": "completed", "run_id": _success_result()["run_id"],
        "source_count": 2, "observations": 3, "unique_platform_videos": 2,
        "new_candidate_count": 1, "scored_videos": 2,
        "provider_call_count": 2, "cached_call_count": 0,
        "uncached_call_count": 2, "external_calls": 2,
        "sdk_retries": 0, "raw_provider_payload_included": False,
    }
    assert calls[:3] == [module.IDENTITY_PATH, module.SETTINGS_PATH, module.API_KEY_PATH]
    assert "private-key" not in repr(result)


def test_lock_contention_defers_without_reading_key_or_running_provider(monkeypatch) -> None:
    module = load_module()
    calls = []
    monkeypatch.setattr(module, "_database_resource", _resource)
    monkeypatch.setattr(
        module, "_windmill_variable",
        lambda path: calls.append(path) or {
            module.IDENTITY_PATH: "scheduled-worker",
            module.SETTINGS_PATH: '{"max_items":5,"date_window_hours":24}',
        }[path],
    )
    monkeypatch.setattr(module, "_single_paid_job", lambda _dsn: _lock(False))
    monkeypatch.setattr(module, "run_live", lambda **_: pytest.fail("must not collect"))

    assert module.main() == {"status": "deferred", "external_calls": 0, "sdk_retries": 0}
    assert module.API_KEY_PATH not in calls


def test_provider_failure_is_sanitized_after_the_last_secret_lookup(monkeypatch) -> None:
    module = load_module()
    calls = []
    monkeypatch.setattr(module, "_database_resource", _resource)
    monkeypatch.setattr(
        module, "_windmill_variable",
        lambda path: calls.append(path) or {
            module.IDENTITY_PATH: "scheduled-worker",
            module.SETTINGS_PATH: '{"max_items":5,"date_window_hours":24}',
            module.API_KEY_PATH: "private-key",
        }[path],
    )
    monkeypatch.setattr(module, "_single_paid_job", lambda _dsn: _lock(True))
    monkeypatch.setattr(
        module, "run_live",
        lambda **_: (_ for _ in ()).throw(RuntimeError("private-key /raw/provider/url")),
    )

    with pytest.raises(RuntimeError, match="TikHub scheduled golden intake failed") as captured:
        module.main()
    assert "private-key" not in str(captured.value)
    assert calls[-1] == module.API_KEY_PATH


def test_key_lookup_runtime_error_is_sanitized(monkeypatch) -> None:
    module = load_module()
    monkeypatch.setattr(module, "_database_resource", _resource)

    def variable(path):
        values = {
            module.IDENTITY_PATH: "scheduled-worker",
            module.SETTINGS_PATH: '{"max_items":5,"date_window_hours":24}',
        }
        if path == module.API_KEY_PATH:
            raise RuntimeError("private-key raw provider URL")
        return values[path]

    monkeypatch.setattr(module, "_windmill_variable", variable)
    monkeypatch.setattr(module, "_single_paid_job", lambda _dsn: _lock(True))
    with pytest.raises(RuntimeError, match="TikHub scheduled golden intake failed") as captured:
        module.main()
    assert "private-key" not in str(captured.value)


def test_all_cached_calls_have_zero_external_calls() -> None:
    module = load_module()
    summary = module._safe_summary(
        {**_success_result(), "cached_call_count": 2, "uncached_call_count": 0},
        module.UUID(_success_result()["run_id"]),
    )
    assert summary["provider_call_count"] == 2
    assert summary["cached_call_count"] == 2
    assert summary["external_calls"] == 0


@pytest.mark.parametrize(
    "result",
    [
        {**_success_result(), "cached_call_count": True, "uncached_call_count": 1},
        {**_success_result(), "cached_call_count": 1, "uncached_call_count": 0},
    ],
)
def test_summary_requires_exact_non_boolean_call_counts(result) -> None:
    module = load_module()
    with pytest.raises(RuntimeError, match="invalid summary"):
        module._safe_summary(result, module.UUID(_success_result()["run_id"]))


def test_summary_rejects_novelty_count_above_unique_batch() -> None:
    module = load_module()
    with pytest.raises(RuntimeError, match="invalid summary"):
        module._safe_summary(
            {**_success_result(), "new_candidate_count": 3},
            module.UUID(_success_result()["run_id"]),
        )


@pytest.mark.parametrize("identity", ["worker" + chr(10) + "other", "worker" + chr(9) + "other"])
def test_worker_identity_rejects_control_characters(identity) -> None:
    module = load_module()
    with pytest.raises(ValueError, match="identity is invalid"):
        module._worker_identity(identity)


@pytest.mark.parametrize(
    "row",
    [None, ("partial", "l0l1_discovery", "douyin"), ("failed", "l0l1_discovery", "douyin"), ("success", "other", "douyin")],
)
def test_pipeline_run_must_be_successful_douyin_l0l1_discovery(monkeypatch, row) -> None:
    module = load_module()

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def execute(self, *_): pass
        def fetchone(self): return row

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def cursor(self): return Cursor()

    monkeypatch.setattr(module.psycopg, "connect", lambda _: Connection())
    with pytest.raises(RuntimeError, match="did not complete successfully"):
        module._completed_run("dsn", module.UUID(_success_result()["run_id"]))


def test_source_declares_fixed_dependency_and_no_public_main_parameters() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    lock = SCRIPT.with_suffix(".script.lock").read_text(encoding="utf-8")
    import re
    commit = re.search(r"douyin-research-platform@([0-9a-f]{40})", source).group(1)
    assert '# requires-python = "==3.14.*"' in source
    assert commit == "e32581b9f771ea75a4d53f579df1f83dac60521a"
    assert f"douyin-research-platform@{commit}" in lock
    assert "def main() -> dict[str, object]:" in source
    assert "WM_END_USER_EMAIL" not in source
    assert "max_retries" not in source
