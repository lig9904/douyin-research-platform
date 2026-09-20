from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import httpx
import pytest


PATH = Path(__file__).parents[1] / "scripts/volcengine/ark_discovery.py"
SPEC = importlib.util.spec_from_file_location("ark_discovery_test", PATH)
assert SPEC and SPEC.loader
discovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(discovery)


def completed_payload(*, content: str = '{"probe":"ark-discovery-v1","status":"ok"}'):
    return {
        "model": "ep-returned-model",
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18, "request_id": "must-not-pass"},
    }


def test_discovery_body_is_fixed_small_and_has_no_business_input() -> None:
    body = discovery.discovery_request_body("ep-test")
    assert body["model"] == "ep-test"
    assert body["temperature"] == 0 and body["max_tokens"] == 64 and body["stream"] is False
    assert body["messages"][-1]["content"] == '{"probe":"ark-discovery-v1"}'
    schema = body["response_format"]["json_schema"]
    assert body["response_format"]["type"] == "json_schema"
    assert schema["strict"] is True
    assert schema["schema"]["additionalProperties"] is False
    assert "title" not in repr(body).lower()
    assert "transcript" not in repr(body).lower()


def test_discovery_sends_one_fixed_request_and_returns_only_protocol_facts() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=completed_payload())

    with httpx.Client(
        base_url=discovery.ARK_BASE_URL,
        follow_redirects=False,
        transport=httpx.MockTransport(handler),
    ) as client:
        result = discovery.discover(api_key="secret-never-output", endpoint_id="ep-test", client=client)
    assert result == {
        "http_status": 200,
        "response_model": "ep-returned-model",
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        "schema_valid": True,
    }
    assert len(calls) == 1
    wire = calls[0]
    assert str(wire.url) == discovery.ARK_BASE_URL + discovery.ARK_CHAT_COMPLETIONS_PATH
    assert wire.headers["authorization"] == "Bearer secret-never-output"
    sent = json.loads(wire.content)
    assert sent == discovery.discovery_request_body("ep-test")
    assert "secret-never-output" not in repr(result)
    assert "must-not-pass" not in repr(result)


def test_default_client_disables_environment_trust_and_retries(monkeypatch) -> None:
    construction: dict[str, object] = {}
    transport_retries: list[int] = []

    class Client:
        base_url = httpx.URL(discovery.ARK_BASE_URL)
        follow_redirects = False

        def __init__(self, **kwargs):
            construction.update(kwargs)

        def post(self, *_args, **_kwargs):
            return httpx.Response(200, json=completed_payload())

        def close(self):
            return None

    monkeypatch.setattr(discovery.httpx, "HTTPTransport", lambda *, retries: transport_retries.append(retries) or object())
    monkeypatch.setattr(discovery.httpx, "Client", Client)
    result = discovery.discover(api_key="secret-never-output", endpoint_id="ep-test")
    assert result["schema_valid"] is True
    assert construction["base_url"] == discovery.ARK_BASE_URL
    assert construction["follow_redirects"] is False
    assert construction["trust_env"] is False
    assert transport_retries == [0]


@pytest.mark.parametrize(
    ("status", "payload", "expected_model", "expected_usage"),
    [
        (401, {"model": "leaky", "error": {"message": "do not expose"}}, None, None),
        (200, {"model": "ep-returned-model", "choices": [{"message": {"content": "not json"}}]}, "ep-returned-model", None),
        (200, completed_payload(content='{"probe":"wrong","status":"ok"}'), "ep-returned-model", {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}),
    ],
)
def test_discovery_redacts_non_success_and_reports_schema_failure(
    status: int, payload: dict[str, object], expected_model: str | None, expected_usage: object,
) -> None:
    with httpx.Client(
        base_url=discovery.ARK_BASE_URL,
        follow_redirects=False,
        transport=httpx.MockTransport(lambda _: httpx.Response(status, json=payload)),
    ) as client:
        result = discovery.discover(api_key="secret-never-output", endpoint_id="ep-test", client=client)
    assert result == {
        "http_status": status,
        "response_model": expected_model,
        "usage": expected_usage,
        "schema_valid": False,
    }
    assert "do not expose" not in repr(result)
    assert "secret-never-output" not in repr(result)


@pytest.mark.parametrize("response_model", [None, "has spaces", "x" * 129, "secret-never-output"])
def test_200_strict_success_with_missing_invalid_or_reflected_model_cannot_succeed(
    response_model: object, monkeypatch, capsys,
) -> None:
    raw = completed_payload()
    raw["model"] = response_model
    with httpx.Client(
        base_url=discovery.ARK_BASE_URL,
        follow_redirects=False,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=raw)),
    ) as client:
        result = discovery.discover(api_key="secret-never-output", endpoint_id="ep-test", client=client)
    assert result["http_status"] == 200
    assert result["schema_valid"] is True
    assert result["response_model"] is None
    monkeypatch.setattr(discovery, "discover_from_environment", lambda: result)
    assert discovery.main(["--live"]) == 2
    assert json.loads(capsys.readouterr().out)["response_model"] is None


def test_main_accepts_only_a_safe_nonempty_response_model(monkeypatch, capsys) -> None:
    result = {
        "http_status": 200,
        "response_model": "ep-returned-model",
        "usage": {"prompt_tokens": 11, "completion_tokens": 7},
        "schema_valid": True,
    }
    monkeypatch.setattr(discovery, "discover_from_environment", lambda: result)
    assert discovery.main(["--live"]) == 0
    assert json.loads(capsys.readouterr().out) == result


def test_discovery_rejects_wrong_origin_or_redirecting_client_before_call() -> None:
    calls: list[httpx.Request] = []
    with httpx.Client(base_url="https://wrong.invalid", transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(200))) as client:
        with pytest.raises(discovery.DiscoveryConfigurationError, match="fixed"):
            discovery.discover(api_key="x", endpoint_id="ep", client=client)
    with httpx.Client(base_url=discovery.ARK_BASE_URL, follow_redirects=True, transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(200))) as client:
        with pytest.raises(discovery.DiscoveryConfigurationError, match="fixed"):
            discovery.discover(api_key="x", endpoint_id="ep", client=client)
    assert calls == []


def test_environment_gate_and_main_never_open_network_without_both_gates(monkeypatch, capsys) -> None:
    monkeypatch.delenv(discovery.DISCOVERY_CONFIRMATION_ENV, raising=False)
    monkeypatch.delenv("VOLCENGINE_ARK_API_KEY", raising=False)
    monkeypatch.delenv("VOLCENGINE_ARK_ENDPOINT_ID", raising=False)
    with pytest.raises(discovery.DiscoveryConfigurationError, match="ARK_DISCOVERY_LIVE"):
        discovery.discover_from_environment()
    with pytest.raises(SystemExit, match="2"):
        discovery.main([])
    captured = capsys.readouterr()
    assert "response_model" not in captured.out  # argparse rejects before any diagnostic request.

    monkeypatch.setenv(discovery.DISCOVERY_CONFIRMATION_ENV, "YES")
    monkeypatch.setenv("VOLCENGINE_ARK_API_KEY", "secret-never-output")
    monkeypatch.setenv("VOLCENGINE_ARK_ENDPOINT_ID", "ep-test")
    monkeypatch.setattr(discovery, "discover", lambda **_: (_ for _ in ()).throw(discovery.DiscoveryTransportError("raw body")))
    assert discovery.main(["--live"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result == {"http_status": None, "response_model": None, "usage": None, "schema_valid": False}
    assert "secret-never-output" not in repr(result)
