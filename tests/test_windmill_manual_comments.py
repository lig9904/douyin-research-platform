from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

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


def request(**overrides):
    values = {
        "video_platform_id": "synthetic-video",
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
        ({"max_pages": 3}, "max_pages"),
        ({"max_items": 41}, "max_items"),
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
            raise module.ProviderRateLimitError(
                "balance=private-total request_id=private-id",
                retry_after=7,
            )

    guarded = module._RedactingTransport(LeakyTransport())
    with pytest.raises(module.ProviderRateLimitError) as caught:
        guarded.call(None, {})

    assert str(caught.value) == "TikHub request was rate limited"
    assert caught.value.retry_after == 7
    assert "balance" not in str(caught.value)
    assert "request_id" not in str(caught.value)

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
