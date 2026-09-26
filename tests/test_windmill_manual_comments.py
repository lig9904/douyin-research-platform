from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest


SCRIPT = (
    Path(__file__).parents[1]
    / "windmill/f/content_research/collectors/manual_comment_collection.py"
)
SPEC = importlib.util.spec_from_file_location("manual_comment_collection", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)
PROJECT_ID = str(uuid4())


def request(**overrides):
    values = {
        "video_platform_id": "synthetic-video",
        "project_id": PROJECT_ID,
        "execute": False,
        "confirmation": "",
        "count": 20,
        "max_pages": 1,
        "max_items": 20,
    }
    values.update(overrides)
    return module.ManualRequest(**values)


def test_preview_never_runs_preflight_reads_secret_or_collects() -> None:
    calls = []

    result = module._execute(
        request(),
        preflight=lambda _video: calls.append("preflight"),
        load_api_key=lambda: calls.append("secret") or "should-not-load",
        collect=lambda _key, _request: calls.append("collect"),
    )

    assert calls == []
    assert result == {
        "status": "preview",
        "execute": False,
        "count": 20,
        "max_pages": 1,
        "max_items": 20,
        "maximum_external_calls": 1,
        "maximum_estimated_cost_usd": 0.001,
        "sdk_retries": 0,
        "llm_calls": 0,
        "rebuild_features_only": False,
        "cursor": "0",
    }
    assert "video_platform_id" not in result


def test_wrong_confirmation_stops_before_database_and_secret() -> None:
    calls = []
    with pytest.raises(PermissionError, match="exact paid-operation confirmation"):
        module._execute(
            request(execute=True, confirmation="yes"),
            preflight=lambda _video: calls.append("preflight"),
            load_api_key=lambda: calls.append("secret") or "key",
            collect=lambda _key, _request: calls.append("collect"),
        )
    assert calls == []


def test_preflight_failure_stops_before_secret() -> None:
    calls = []

    def fail_preflight(_video):
        calls.append("preflight")
        raise RuntimeError("budget missing")

    with pytest.raises(RuntimeError, match="budget missing"):
        module._execute(
            request(execute=True, confirmation=module.CONFIRMATION),
            preflight=fail_preflight,
            load_api_key=lambda: calls.append("secret") or "key",
            collect=lambda _key, _request: calls.append("collect"),
        )
    assert calls == ["preflight"]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"count": 21}, "count"),
        ({"max_pages": 11}, "max_pages"),
        ({"max_items": 201}, "max_items"),
        ({"cursor": "bad-token"}, "cursor"),
        ({"max_pages": 2, "max_items": 25}, "multiple of count"),
        ({"count": 5, "max_pages": 1, "max_items": 6}, r"count \* max_pages"),
    ],
)
def test_hard_bounds(overrides, message) -> None:
    with pytest.raises(ValueError, match=message):
        module._execute(
            request(**overrides),
            preflight=lambda _video: None,
            load_api_key=lambda: "key",
            collect=lambda _key, _request: None,
        )


def test_invalid_project_scope_stops_before_database_or_secret() -> None:
    calls = []
    with pytest.raises(ValueError, match="project_id"):
        module._execute(
            request(execute=True, confirmation=module.CONFIRMATION, project_id="not-a-uuid"),
            preflight=lambda _video: calls.append("preflight"),
            load_api_key=lambda: calls.append("secret") or "key",
            collect=lambda _key, _request: calls.append("collect"),
        )
    assert calls == []


def test_execution_requires_project_scope_before_database_or_secret() -> None:
    calls = []
    with pytest.raises(ValueError, match="project_id is required"):
        module._execute(
            request(execute=True, confirmation=module.CONFIRMATION, project_id=""),
            preflight=lambda _video: calls.append("preflight"),
            load_api_key=lambda: calls.append("secret") or "key",
            collect=lambda _key, _request: calls.append("collect"),
        )
    assert calls == []


def test_feature_rebuild_uses_stored_evidence_without_secret_or_paid_collect() -> None:
    calls = []
    result = module._execute(
        request(execute=True, rebuild_features_only=True,
                confirmation=module.REBUILD_CONFIRMATION),
        preflight=lambda _video: calls.append("preflight"),
        load_api_key=lambda: calls.append("secret") or "key",
        collect=lambda _key, _request: calls.append("collect"),
        rebuild_features=lambda _request: calls.append("rebuild") or SimpleNamespace(sampled_comment_count=35),
    )
    assert calls == ["preflight", "rebuild"]
    assert result["status"] == "features_rebuilt"
    assert result["external_pages"] == 0
    assert result["estimated_api_cost_usd"] == 0


def test_paid_collection_reports_feature_failure_without_requesting_paid_retry() -> None:
    summary = SimpleNamespace(
        comments_returned=20, new_comments=20, observations_inserted=20,
        duplicate_observations=0, pages_fetched=1, cached_pages=0,
        external_pages=1, cross_page_duplicates_removed=0,
        estimated_api_cost_usd=0.001, cached=False,
    )
    result = module._execute(
        request(execute=True, confirmation=module.CONFIRMATION),
        preflight=lambda _video: None,
        load_api_key=lambda: "key",
        collect=lambda _key, _request: module._CollectionOutcome(summary, "pending", "DatabaseError"),
    )
    assert result["status"] == "collected_feature_pending"
    assert result["feature_error_type"] == "DatabaseError"
    assert result["external_pages"] == 1


def test_project_preflight_requires_real_member_and_accepted_video(monkeypatch) -> None:
    class Cursor:
        def __init__(self):
            self.statements = []
            self.rows = [(uuid4(),), None]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, sql, _params):
            self.statements.append(sql)

        def fetchone(self):
            return self.rows.pop(0)

    class Connection:
        def __init__(self, cursor):
            self._cursor = cursor

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def cursor(self):
            return self._cursor

    cursor = Cursor()
    monkeypatch.setattr(module.psycopg, "connect", lambda _dsn: Connection(cursor))
    monkeypatch.setenv("WM_END_USER_EMAIL", "viewer@example.com")
    with pytest.raises(PermissionError, match="accepted project video"):
        module._preflight("test-dsn", "7684244242625316517", str(uuid4()))
    assert len(cursor.statements) == 2
    assert "inclusion.status='accepted'" in cursor.statements[1]
    assert "member.role in ('owner','admin','researcher')" in cursor.statements[1]


def test_execution_returns_only_redacted_aggregates() -> None:
    calls = []

    def collect(key, bounded):
        calls.append(("collect", key, bounded.max_pages))
        return SimpleNamespace(
            run_id="internal-run-id",
            video_platform_id="sensitive-video",
            comments_returned=29,
            new_comments=29,
            observations_inserted=29,
            duplicate_observations=0,
            pages_fetched=2,
            cached_pages=0,
            external_pages=2,
            cross_page_duplicates_removed=10,
            estimated_api_cost_usd=0.002,
            cached=False,
        )

    result = module._execute(
        request(
            execute=True,
            confirmation=module.CONFIRMATION,
            max_pages=2,
            max_items=40,
        ),
        preflight=lambda _video: calls.append(("preflight",)),
        load_api_key=lambda: "secret-value",
        collect=collect,
    )

    assert calls[0] == ("preflight",)
    assert calls[1] == ("collect", "secret-value", 2)
    assert result["status"] == "completed"
    assert result["external_pages"] == 2
    assert result["estimated_api_cost_usd"] == 0.002
    assert "run_id" not in result
    assert "video_platform_id" not in result
    assert "secret-value" not in repr(result)



def test_upstream_exception_text_is_redacted_before_provider_logging() -> None:
    class LeakyTransport:
        def call(self, _spec, _kwargs):
            error = module.ProviderRateLimitError(
                "balance=private-total request_id=private-id",
                retry_after=7,
            )
            error.provider_attempts = ({"http_status": 429, "secret": "private-key"},)
            raise error

    guarded = module._RedactingTransport(LeakyTransport())
    with pytest.raises(module.ProviderRateLimitError) as caught:
        guarded.call(None, {})

    assert str(caught.value) == "TikHub request was rate limited"
    assert caught.value.retry_after == 7
    assert "balance" not in str(caught.value)
    assert "request_id" not in str(caught.value)
    assert caught.value.provider_attempts == ({"http_status": 429},)
    assert "private-key" not in repr(caught.value.provider_attempts)

def test_windmill_metadata_keeps_paid_flow_manual_and_serial() -> None:
    flow = (
        Path(__file__).parents[1]
        / "windmill/f/content_research/flows/manual_comment_collection.flow/flow.yaml"
    ).read_text()
    script_metadata = SCRIPT.with_suffix(".script.yaml").read_text()

    assert "\nschedule:" not in flow.lower()
    assert not flow.lower().startswith("schedule:")
    assert "default: false" in flow
    assert "\n  concurrent_limit: 1\n" in flow
    assert "concurrent_limit: 1" in script_metadata
    assert "maximum: 2" in flow
    assert "$var:" not in flow
    assert "expr: flow_input.project_id" in flow
    assert "project_id:" in script_metadata
    assert "expr: flow_input.rebuild_features_only" in flow
