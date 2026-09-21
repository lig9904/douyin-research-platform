#!/usr/bin/env python3
"""One-shot, privacy-free Ark response-model and strict-schema diagnostic.

This is intentionally separate from the L3 provider and worker.  It sends one
fixed synthetic JSON request solely to establish the endpoint's returned
``model`` value and whether the endpoint accepts and obeys strict JSON Schema.
It never sends research evidence, user content, account identifiers, or an
API key in output.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Mapping
from typing import Any

import httpx


ARK_BASE_URL = "https://ark.cn-beijing.volces.com"
ARK_CHAT_COMPLETIONS_PATH = "/api/v3/chat/completions"
DISCOVERY_CONFIRMATION_ENV = "ARK_DISCOVERY_LIVE"
DISCOVERY_CONFIRMATION_VALUE = "YES"
DISCOVERY_TIMEOUT_SECONDS = 20
_RESPONSE_MODEL_RE = re.compile(r"[A-Za-z0-9._:/-]{1,128}\Z")
# The flag and environment value are an operator-audit gate for this diagnostic,
# not an authorization mechanism. Access remains controlled by the Ark key and
# its account/endpoint permissions.


class DiscoveryConfigurationError(ValueError):
    """A non-secret local gate or required field is missing."""


class DiscoveryTransportError(RuntimeError):
    """A redacted request/response failure."""


def discovery_request_body(endpoint_id: str) -> dict[str, object]:
    """Return the fixed, non-business payload used by the one-call probe."""
    if not isinstance(endpoint_id, str) or not endpoint_id.strip():
        raise DiscoveryConfigurationError("Ark discovery endpoint is required")
    return {
        "model": endpoint_id,
        "temperature": 0,
        "max_tokens": 64,
        "stream": False,
        "messages": [
            {
                "role": "system",
                "content": "Return only the requested strict JSON object.",
            },
            {
                "role": "user",
                "content": '{"probe":"ark-discovery-v1"}',
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "ark_discovery",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "probe": {"type": "string", "const": "ark-discovery-v1"},
                        "status": {"type": "string", "enum": ["ok"]},
                    },
                    "required": ["probe", "status"],
                    "additionalProperties": False,
                },
            },
        },
    }


def discover(
    *,
    api_key: str,
    endpoint_id: str,
    client: httpx.Client | None = None,
) -> dict[str, object]:
    """Make exactly one request and return only non-sensitive protocol facts."""
    if not isinstance(api_key, str) or not api_key.strip():
        raise DiscoveryConfigurationError("Ark discovery API key is required")
    body = discovery_request_body(endpoint_id)
    owned_client = client is None
    http = client or httpx.Client(
        base_url=ARK_BASE_URL,
        timeout=DISCOVERY_TIMEOUT_SECONDS,
        follow_redirects=False,
        trust_env=False,
        transport=httpx.HTTPTransport(retries=0),
    )
    try:
        if http.base_url != httpx.URL(ARK_BASE_URL) or http.follow_redirects:
            raise DiscoveryConfigurationError("Ark discovery client is not fixed to the Ark origin")
        try:
            response = http.post(
                ARK_CHAT_COMPLETIONS_PATH,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json=body,
            )
        except httpx.HTTPError:
            raise DiscoveryTransportError("Ark discovery request failed") from None
        return _result_from_response(response, api_key=api_key)
    finally:
        if owned_client:
            http.close()


def discover_from_environment() -> dict[str, object]:
    """Apply the second, exact local gate before constructing an HTTP client."""
    if os.environ.get(DISCOVERY_CONFIRMATION_ENV) != DISCOVERY_CONFIRMATION_VALUE:
        raise DiscoveryConfigurationError(
            f"set {DISCOVERY_CONFIRMATION_ENV}={DISCOVERY_CONFIRMATION_VALUE} before Ark discovery"
        )
    missing = [
        name
        for name in ("VOLCENGINE_ARK_API_KEY", "VOLCENGINE_ARK_ENDPOINT_ID")
        if not os.environ.get(name)
    ]
    if missing:
        raise DiscoveryConfigurationError("Ark discovery configuration is unavailable")
    return discover(
        api_key=os.environ["VOLCENGINE_ARK_API_KEY"],
        endpoint_id=os.environ["VOLCENGINE_ARK_ENDPOINT_ID"],
    )


def _result_from_response(
    response: httpx.Response,
    *,
    api_key: str,
) -> dict[str, object]:
    result: dict[str, object] = {
        "http_status": response.status_code,
        "response_model": None,
        "usage": None,
        "schema_valid": False,
    }
    if response.status_code < 200 or response.status_code >= 300:
        return result
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError):
        return result
    if not isinstance(payload, Mapping):
        return result
    model = payload.get("model")
    if _safe_response_model(model, api_key=api_key):
        result["response_model"] = model
    result["usage"] = _safe_usage(payload.get("usage"))
    result["schema_valid"] = _strict_probe_schema_valid(payload)
    return result


def _safe_response_model(value: object, *, api_key: str) -> bool:
    """Allow only a bounded identifier that cannot reflect the API key."""
    return (
        isinstance(value, str)
        and _RESPONSE_MODEL_RE.fullmatch(value) is not None
        and (not api_key or api_key not in value)
    )


def _safe_usage(value: object) -> dict[str, int] | None:
    if not isinstance(value, Mapping):
        return None
    # Keep only ordinary token counters; never pass through provider metadata.
    keys = ("prompt_tokens", "completion_tokens", "total_tokens")
    usage = {key: value[key] for key in keys if type(value.get(key)) is int and value[key] >= 0}
    return usage or None


def _strict_probe_schema_valid(payload: Mapping[str, Any]) -> bool:
    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], Mapping):
        return False
    message = choices[0].get("message")
    if not isinstance(message, Mapping) or message.get("refusal"):
        return False
    content = message.get("content")
    if not isinstance(content, str):
        return False
    try:
        candidate = json.loads(content)
    except json.JSONDecodeError:
        return False
    return candidate == {"probe": "ark-discovery-v1", "status": "ok"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="allow one paid discovery request")
    args = parser.parse_args(argv)
    if not args.live:
        parser.error("Ark discovery requires the explicit --live flag")
    try:
        result = discover_from_environment()
    except (DiscoveryConfigurationError, DiscoveryTransportError):
        # Do not expose environment values, raw HTTP bodies, or exception text.
        print(json.dumps({"http_status": None, "response_model": None, "usage": None, "schema_valid": False}))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if (
        result["http_status"] == 200
        and result["schema_valid"] is True
        and _safe_response_model(result["response_model"], api_key="")
    ) else 2


if __name__ == "__main__":
    sys.exit(main())
