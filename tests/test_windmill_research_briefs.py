from __future__ import annotations

import importlib.util
import inspect
import sys
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
ROOT = Path(__file__).parents[1]
COLLECTORS = ROOT / "windmill/f/content_research/collectors"
FLOW = ROOT / "windmill/f/content_research/flows/research_brief_cycle.flow/flow.yaml"


def _load(stem: str):
    path = COLLECTORS / f"{stem}.py"
    spec = importlib.util.spec_from_file_location(f"{stem}_windmill_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_flow_only_accepts_brief_id_and_never_auto_runs_analysis() -> None:
    raw = FLOW.read_text()
    assert raw.count("path: f/content_research/collectors/run_research_brief") == 1
    assert raw.count("path: f/content_research/collectors/process_comment_batch") == 1
    assert raw.count("path: f/content_research/collectors/select_cycle_media") == 1
    assert "properties:\n    brief_id:" in raw
    assert "required:\n    - brief_id" in raw
    assert "run_reviewed_asr" not in raw
    assert "run_reviewed_l3" not in raw
    assert "dispatch_reviewed" not in raw
    assert "approval creation" in raw.lower()
    assert "parallel: false" in raw


def test_dispatcher_has_no_public_input_or_provider_secret() -> None:
    dispatch = _load("dispatch_due_research_briefs")
    source = inspect.getsource(dispatch)
    assert not inspect.signature(dispatch.main).parameters
    assert "limit 5" in source.lower()
    assert "run_flow_async" in source
    assert "tikhub_api_key" not in source
    assert "httpx" not in source
    schedule = (COLLECTORS / "dispatch_due_research_briefs.schedule.yaml").read_text()
    assert "enabled: false" in schedule


def test_scoped_dispatch_only_selects_active_project_and_organization() -> None:
    dispatch = _load("dispatch_due_research_briefs")
    source = inspect.getsource(dispatch._due).lower()
    assert "left join research_project as project" in source
    assert "left join research_organization as organization" in source
    assert "brief.project_id is null" in source
    assert "project.status='active' and organization.status='active'" in source


def test_dispatcher_keeps_legacy_due_briefs_but_excludes_paused_project_fixture(monkeypatch) -> None:
    dispatch = _load("dispatch_due_research_briefs")
    legacy_id = uuid4()
    active_project_id = uuid4()

    class Cursor:
        def execute(self, statement: str, _params=None) -> None:
            if statement.strip().lower().startswith("select brief.id"):
                # The fake database only returns rows that satisfy the asserted
                # SQL predicate: legacy and active-project briefs.  A paused
                # project brief is deliberately absent.
                assert "brief.project_id is null" in statement
                assert "project.status='active' and organization.status='active'" in statement

        def fetchall(self):
            return [{"id": legacy_id}, {"id": active_project_id}]

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

    class Connection:
        def cursor(self):
            return Cursor()

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

    monkeypatch.setattr(dispatch.psycopg, "connect", lambda **_kwargs: Connection())
    assert dispatch._due({"host": "db", "port": 5432, "user": "u", "password": "p", "dbname": "d", "sslmode": "prefer"}) == [
        str(legacy_id), str(active_project_id)
    ]


def test_dispatcher_failure_category_does_not_leak_exception_message() -> None:
    dispatch = _load("dispatch_due_research_briefs")
    failure = dispatch._safe_failure("query", ValueError("password=do-not-leak"))
    assert str(failure) == "research brief dispatch query failed [ValueError]"
    assert "do-not-leak" not in str(failure)


def test_runner_summary_rejects_any_automatic_analysis_flag() -> None:
    runner = _load("run_research_brief")
    result = {
        "run_id": str(uuid4()),
        "observations": 2,
        "unique_platform_videos": 2,
        "new_candidate_count": 1,
        "scored_videos": 2,
        "provider_call_count": 2,
        "cached_call_count": 0,
        "uncached_call_count": 2,
        "max_external_calls": 2,
        "sdk_retries": 0,
        "collect_comments": True,
        "collect_media": True,
        "review_required": True,
        "auto_submit_asr": False,
        "auto_submit_l3": False,
    }
    safe = runner._safe_result(result, uuid4())
    assert safe["external_calls"] == 2
    assert safe["raw_provider_payload_included"] is False
    project_result = {
        **result,
        "new_candidate_count": 0,
        "scored_videos": 2,
        "relevant_new_project_count": 2,
        "subject_scoring_status": "project_subject_l1",
        "collect_comments": False,
        "collect_media": False,
        "review_required": False,
    }
    project_id = uuid4()
    project_safe = runner._safe_result(project_result, uuid4(), project_id=project_id)
    assert project_safe["new_candidate_count"] == 0
    assert project_safe["relevant_new_project_count"] == 2
    assert project_safe["subject_scoring_status"] == "project_subject_l1"
    project_result["relevant_new_project_count"] = "2"
    with pytest.raises(RuntimeError, match="project candidate count"):
        runner._safe_result(project_result, uuid4(), project_id=project_id)
    project_result["relevant_new_project_count"] = 2
    project_result["collect_comments"] = True
    with pytest.raises(RuntimeError, match="project analysis boundary"):
        runner._safe_result(project_result, uuid4(), project_id=project_id)
    project_result["collect_comments"] = False
    project_result.pop("subject_scoring_status")
    with pytest.raises(RuntimeError, match="project scoring contract"):
        runner._safe_result(project_result, uuid4(), project_id=project_id)
    result["auto_submit_l3"] = True
    with pytest.raises(RuntimeError, match="analysis boundary"):
        runner._safe_result(result, uuid4())


def test_runner_failure_summary_is_allowlisted_and_does_not_leak_message() -> None:
    runner = _load("run_research_brief")
    failure = RuntimeError("video=private url=https://private.invalid response=secret")
    failure.research_failure_summary = {
        "failure_schema": "provider_failure_v1",
        "status": "failed",
        "stage": "detail_enrichment",
        "item_count": 5,
        "error_type": "ProviderPermanentError",
        "http_status": 400,
        "provider_error_code": "INVALID_PARAMETER",
        "provider_request_id": "req-safe-400",
        "ledger_logical_call_id": str(uuid4()),
        "response_body": "must not escape",
    }

    safe = runner._safe_failure_summary(failure)

    assert safe["stage"] == "detail_enrichment"
    assert safe["item_count"] == 5
    assert safe["http_status"] == 400
    assert safe["provider_error_code"] == "INVALID_PARAMETER"
    assert "response_body" not in safe
    assert "private" not in str(safe)


def test_runner_uses_fixed_server_resources_and_zero_retry_core() -> None:
    source = (COLLECTORS / "run_research_brief.py").read_text()
    assert "WM_END_USER_EMAIL" not in source
    assert 'API_KEY_PATH = "f/content_research/tikhub_api_key"' in source
    assert 'IDENTITY_PATH = "f/content_research/automation_worker_identity"' in source
    assert "max_external_calls" in source
    assert "raw_provider_payload_included" in source


@pytest.mark.parametrize("project_id", [uuid4(), None])
def test_runner_passes_project_scope_to_live_run(monkeypatch, project_id) -> None:
    runner = _load("run_research_brief")
    brief_run_id = uuid4()
    received: dict[str, object] = {}

    @contextmanager
    def acquired(_dsn: str):
        yield True

    def live_run(**kwargs):
        received.update(kwargs)
        return {
            "run_id": str(uuid4()),
            "observations": 0,
            "unique_platform_videos": 0,
            "new_candidate_count": 0,
            "scored_videos": 0,
            "subject_scoring_status": "project_subject_l1" if project_id else "global_l1",
            "provider_call_count": 0,
            "cached_call_count": 0,
            "uncached_call_count": 0,
            "max_external_calls": 2,
            "sdk_retries": 0,
            "collect_comments": False,
            "collect_media": False,
            "review_required": False,
            "auto_submit_asr": False,
            "auto_submit_l3": False,
        }

    monkeypatch.setattr(runner, "_resource", lambda: {})
    monkeypatch.setattr(runner, "_dsn", lambda _resource: "postgresql://test")
    monkeypatch.setattr(runner, "_variable", lambda _path: "secret" if "api_key" in _path else "worker")
    monkeypatch.setattr(runner, "_single_paid_job", acquired)
    monkeypatch.setattr(
        runner,
        "_claim",
        lambda _dsn, _brief_id, _actor: {
            "brief_run_id": brief_run_id,
            "project_id": project_id,
            "config": {
                "source_type": "low_fan", "target": None,
                "time_window_hours": 24, "max_items": 1,
                "depth": "metadata", "cadence_hours": None,
            },
        },
    )
    monkeypatch.setattr(runner, "run_live", live_run)
    monkeypatch.setattr(runner, "_finish", lambda *_args, **_kwargs: None)

    result = runner.main(str(uuid4()))

    assert result["status"] == "completed"
    assert received["project_id"] == project_id


def test_runner_claim_persists_the_same_project_scope_as_its_brief() -> None:
    runner = _load("run_research_brief")
    source = inspect.getsource(runner._claim)
    assert "project_id = _project_id(row[\"project_id\"])" in source
    assert "brief_id, project_id, brief_version" in source
    assert "brief.project_id is null" in source
