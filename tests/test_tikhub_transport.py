from __future__ import annotations

import httpx
import pytest

from douyin_research.providers.endpoints import EndpointSpec
from douyin_research.providers.errors import (
    ProviderAuthError,
    ProviderBalanceError,
    ProviderPermanentError,
    ProviderRateLimitError,
    ProviderTemporaryError,
)
from douyin_research.providers.transport import TikHubTransport


def _rest_spec() -> EndpointSpec:
    return EndpointSpec(
        key="test.readonly",
        http_method="GET",
        path="/readonly",
        sdk_resource=None,
        sdk_method=None,
        request_style="query",
        cache_ttl_seconds=60,
        paid=False,
    )


def test_rest_fallback_retries_5xx_then_succeeds(monkeypatch) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(503, text="temporary")
        return httpx.Response(200, json={"code": 200, "request_id": "ok", "data": {}})

    monkeypatch.setattr("douyin_research.providers.transport.time.sleep", lambda _: None)
    client = httpx.Client(
        base_url="https://api.tikhub.io",
        transport=httpx.MockTransport(handler),
    )
    transport = TikHubTransport(
        "fake",
        max_retries=3,
        http_client=client,
    )

    result = transport.call(_rest_spec(), {"x": 1})

    assert calls == 3
    assert result.retry_count == 2
    assert result.payload["code"] == 200


def test_rest_fallback_has_hard_retry_limit(monkeypatch) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, text="still down")

    monkeypatch.setattr("douyin_research.providers.transport.time.sleep", lambda _: None)
    client = httpx.Client(
        base_url="https://api.tikhub.io",
        transport=httpx.MockTransport(handler),
    )
    transport = TikHubTransport(
        "fake",
        max_retries=3,
        http_client=client,
    )

    with pytest.raises(ProviderTemporaryError):
        transport.call(_rest_spec(), {})

    assert calls == 3


def test_rest_fallback_honors_retry_after_then_succeeds(monkeypatch) -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json={"code": 200, "request_id": "ok", "data": {}})

    monkeypatch.setattr("douyin_research.providers.transport.time.sleep", sleeps.append)
    client = httpx.Client(base_url="https://api.tikhub.io", transport=httpx.MockTransport(handler))
    transport = TikHubTransport("fake", max_retries=3, http_client=client)

    result = transport.call(_rest_spec(), {})

    assert calls == 2
    assert sleeps == [2.0]
    assert result.retry_count == 1


def test_rest_fallback_exposes_retry_after_at_hard_limit(monkeypatch) -> None:
    monkeypatch.setattr("douyin_research.providers.transport.time.sleep", lambda _: None)
    client = httpx.Client(
        base_url="https://api.tikhub.io",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(429, headers={"Retry-After": "7"})
        ),
    )
    transport = TikHubTransport("fake", max_retries=2, http_client=client)

    with pytest.raises(ProviderRateLimitError) as captured:
        transport.call(_rest_spec(), {})

    assert captured.value.retry_after == 7.0


@pytest.mark.parametrize(
    ("status", "error_type"),
    [(401, ProviderAuthError), (402, ProviderBalanceError)],
)
def test_rest_fallback_does_not_retry_permanent_account_errors(status, error_type) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, text="account error")

    client = httpx.Client(base_url="https://api.tikhub.io", transport=httpx.MockTransport(handler))
    transport = TikHubTransport("fake", max_retries=3, http_client=client)

    with pytest.raises(error_type):
        transport.call(_rest_spec(), {})

    assert calls == 1


def test_rest_400_exposes_only_bounded_failure_diagnostic() -> None:
    client = httpx.Client(
        base_url="https://api.tikhub.io",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                400,
                headers={"x-request-id": "req-400.safe"},
                json={
                    "code": "INVALID_PARAMETER",
                    "message": "video=private-123 url=https://private.invalid",
                },
            )
        ),
    )
    transport = TikHubTransport("fake", max_retries=0, http_client=client)

    with pytest.raises(ProviderPermanentError) as captured:
        transport.call(_rest_spec(), {})

    assert captured.value.provider_diagnostic == {
        "http_status": 400,
        "provider_error_code": "INVALID_PARAMETER",
        "provider_request_id": "req-400.safe",
    }
    assert "private-123" not in str(captured.value)
    assert "private.invalid" not in str(captured.value)
