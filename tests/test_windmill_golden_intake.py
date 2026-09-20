from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).parents[1]
    / "windmill/f/content_research/collectors/manual_golden_intake.py"
)
SPEC = importlib.util.spec_from_file_location("manual_golden_intake", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def request(**overrides):
    values = {
        "execute": False,
        "confirmation": "",
        "max_items": 5,
        "max_external_calls": 2,
        "max_cost_usd": None,
        "date_window_hours": 24,
        "enrich_details": True,
        "force_refresh": False,
    }
    values.update(overrides)
    return module.ManualGoldenRequest(**values)


def execute(req, calls):
    return module._execute(
        req,
        preflight=lambda: calls.append("preflight"),
        authorize=lambda: calls.append("authorize") or "writer@example.org",
        load_api_key=lambda: calls.append("secret") or "private-key",
        collect=lambda _key, _actor, _plan: calls.append("collect") or {
            "run_id": "private-run-id",
            "source_count": 5,
            "observations": 5,
            "unique_platform_videos": 5,
            "scored_videos": 5,
            "max_external_calls": 2,
            "provider_call_count": 2,
            "cached_call_count": 0,
            "uncached_call_count": 2,
            "retry_count": 0,
        },
    )


def test_preview_never_preflights_authorizes_reads_secret_or_collects() -> None:
    calls = []
    result = execute(request(), calls)

    assert calls == []
    assert result["status"] == "preview"
    assert result["external_calls"] == 0
    assert result["max_items"] == 5
    assert result["retry_count"] == 0


def test_wrong_confirmation_stops_before_database_identity_and_secret() -> None:
    calls = []
    with pytest.raises(PermissionError, match="exact paid-operation confirmation"):
        execute(request(execute=True, confirmation="yes"), calls)
    assert calls == []


def test_execution_checks_database_and_actor_before_secret() -> None:
    calls = []
    result = execute(
        request(execute=True, confirmation=module.CONFIRMATION, force_refresh=True),
        calls,
    )

    assert calls == ["preflight", "authorize", "secret", "collect"]
    assert result["status"] == "completed"
    assert result["uncached_call_count"] == 2
    assert result["maximum_cost_usd"] is None
    assert "run_id" not in result
    assert "private-key" not in repr(result)
    assert "writer@example.org" not in repr(result)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"max_items": 6}, "max_items"),
        ({"max_external_calls": 3}, "max_external_calls"),
        ({"max_cost_usd": -0.001}, "max_cost_usd"),
        ({"date_window_hours": 25}, "date_window_hours"),
        ({"max_external_calls": 1, "enrich_details": True}, "at least 2"),
    ],
)
def test_paid_envelope_is_hard_bounded(overrides, message) -> None:
    with pytest.raises(ValueError, match=message):
        execute(request(**overrides), [])


def test_windmill_metadata_keeps_golden_flow_manual_serial_and_server_secret_only() -> None:
    root = Path(__file__).parents[1]
    flow = (
        root / "windmill/f/content_research/flows/manual_golden_intake.flow/flow.yaml"
    ).read_text()
    metadata = SCRIPT.with_suffix(".script.yaml").read_text()
    lock = SCRIPT.with_suffix(".script.lock").read_text()
    source = SCRIPT.read_text()

    assert "\nschedule:" not in flow.lower()
    assert "default: false" in flow
    assert "concurrent_limit: 1" in flow
    assert "default: null" in flow
    assert "maximum: 0.01" not in flow
    assert "maximum: 5" in flow
    assert "maximum: 2" in flow
    assert "$var:" not in flow
    assert "concurrent_limit: 1" in metadata
    assert 'wmill.get_variable(path)' in source
    assert "TIKHUB_API_KEY" not in source
    assert "#psycopg[binary]==3.3.6" in source
    assert "psycopg==3.3.6" in lock
    assert "psycopg-binary==3.3.6" in lock
    assert "tikhub==2.1.1" in lock
    assert "#wmill==1.815.0" in source
    assert "wmill==1.815.0" in lock
