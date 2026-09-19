from __future__ import annotations

import httpx
import pytest

from douyin_research.providers.endpoints import EndpointSpec
from douyin_research.providers.errors import ProviderTemporaryError
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
