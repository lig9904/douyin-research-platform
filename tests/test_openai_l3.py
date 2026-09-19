from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from douyin_research.l3 import L3_SCHEMA_VERSION, L3ProviderRequest
from douyin_research.providers.openai_l3 import (
    OPENAI_L3_DOC_FINGERPRINT,
    OPENAI_L3_PROVIDER,
    OpenAIResponsesL3Provider,
    _decode_json_object,
    _map_completed_response,
    _request_body,
    _require_success_status,
)

SECRET = "synthetic-secret-never-log"
MODEL_ID = "synthetic-reasoning-model"
MODEL_REVISION = "synthetic-reasoning-model-2026-09-01"


def _request(**overrides) -> L3ProviderRequest:
    values = {
        "task_key": "synthetic-l3-task",
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "prompt_version": "l3-prompt-v1",
        "schema_version": L3_SCHEMA_VERSION,
        "input_fingerprint": "input-fingerprint",
        "evidence_bundle": {
            "modalities": ["metadata", "comments"],
            "summary": "privacy-reviewed synthetic evidence",
            "privacy_review": {"reviewed": True, "version": "privacy-v1"},
        },
    }
    values.update(overrides)
    return L3ProviderRequest(**values)


def _structured(**overrides):
    value = {
        "task_key": "synthetic-l3-task",
        "prompt_version": "l3-prompt-v1",
        "schema_version": L3_SCHEMA_VERSION,
        "input_fingerprint": "input-fingerprint",
        "evidence_modalities": ["metadata", "comments"],
        "narrative_structure": ["setup then resolution"],
        "hook_functions": ["opens an information gap"],
        "comment_semantics": ["sample expresses curiosity"],
        "case_comparisons": ["no comparison evidence supplied"],
        "mechanism_hypotheses": ["curiosity may support retention"],
        "ip_fit": ["requires editorial review"],
        "limitations": ["based on a bounded evidence sample"],
    }
    value.update(overrides)
    return value


def _response(**overrides):
    value = {
        "status": "completed",
        "model": MODEL_REVISION,
        "output": [
            {
                "type": "message",
                "content": [
                    {"type": "output_text", "text": json.dumps(_structured())}
                ],
            }
        ],
        "usage": {"input_tokens": 1000, "output_tokens": 500},
    }
    value.update(overrides)
    return value


def _provider() -> OpenAIResponsesL3Provider:
    return OpenAIResponsesL3Provider(
        model_id=MODEL_ID,
        model_revision=MODEL_REVISION,
        cost_currency="USD",
        pricing_version="synthetic-pricing-v1",
        input_cost_per_million_tokens="2.50",
        output_cost_per_million_tokens="10.00",
    )


def _map(payload, *, request=None):
    return _map_completed_response(
        payload,
        request or _request(),
        model_revision=MODEL_REVISION,
        cost_currency="USD",
        input_rate=Decimal("2.50"),
        output_rate=Decimal("10.00"),
    )


def test_adapter_is_permanently_fail_closed_without_transport() -> None:
    provider = _provider()

    with pytest.raises(ValueError, match="not production ready"):
        provider.generate(_request())

    assert provider.contract.production_ready is False
    assert provider.pricing_version == "synthetic-pricing-v1"
    assert not hasattr(provider, "_generate_with_transport")
    assert not hasattr(provider, "_client")
    assert not hasattr(provider, "_api_key")
    assert SECRET not in repr(provider)


def test_production_ready_and_secret_cannot_be_injected_by_constructor() -> None:
    common = {
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "cost_currency": "USD",
        "pricing_version": "synthetic-pricing-v1",
        "input_cost_per_million_tokens": "2.50",
        "output_cost_per_million_tokens": "10.00",
    }
    with pytest.raises(TypeError, match="production_ready"):
        OpenAIResponsesL3Provider(**common, production_ready=True)
    with pytest.raises(TypeError, match="api_key"):
        OpenAIResponsesL3Provider(**common, api_key=SECRET)


def test_offline_wire_codec_builds_strict_request_and_maps_result() -> None:
    calls = []

    def handler(request):
        calls.append(request)
        body = json.loads(request.content)
        assert "Authorization" not in request.headers
        assert body["model"] == MODEL_REVISION
        assert body["store"] is False
        assert body["text"]["format"]["type"] == "json_schema"
        assert body["text"]["format"]["strict"] is True
        assert body["text"]["format"]["schema"]["additionalProperties"] is False
        envelope = json.loads(body["input"])
        assert envelope["prompt_version"] == "l3-prompt-v1"
        assert envelope["input_fingerprint"] == "input-fingerprint"
        assert envelope["evidence"]["summary"].startswith("privacy-reviewed")
        return httpx.Response(200, json=_response())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        response = client.post(
            "https://offline.invalid/v1/responses",
            json=_request_body(_request()),
        )
    _require_success_status(response.status_code)
    result = _map(_decode_json_object(response.content))

    assert len(calls) == 1
    assert result.result.model_id == MODEL_ID
    assert result.result.model_revision == MODEL_REVISION
    assert result.result.privacy_reviewed is True
    assert result.result.mechanism_hypotheses == (
        "curiosity may support retention",
    )
    assert result.cost.api_cost == 0
    assert result.cost.asr_cost == 0
    assert result.cost.llm_cost == Decimal("0.007500")
    assert result.cost.currency == "USD"
    assert result.cost.basis == "estimated"


def test_model_and_evidence_fail_before_production_gate() -> None:
    provider = _provider()
    with pytest.raises(ValueError, match="model does not match"):
        provider.generate(_request(model_revision="different-revision"))

    evidence = {
        "modalities": ["metadata"],
        "privacy_review": {"reviewed": False, "version": "privacy-v1"},
    }
    with pytest.raises(ValueError, match="privacy review is not complete"):
        provider.generate(_request(evidence_bundle=evidence))

    evidence["privacy_review"] = {"reviewed": True, "version": "privacy-v1"}
    evidence["modalities"] = ["metadata", "metadata"]
    with pytest.raises(ValueError, match="modalities must be unique"):
        provider.generate(_request(evidence_bundle=evidence))


@pytest.mark.parametrize(
    ("response", "message"),
    [
        ({"status": "in_progress"}, "did not complete"),
        ({"model": "different-revision"}, "revision does not match"),
        ({"output": []}, "exactly one output text"),
        ({"usage": {}}, "token usage"),
    ],
)
def test_invalid_provider_responses_fail_closed(response, message) -> None:
    with pytest.raises((ValueError, RuntimeError), match=message):
        _map(_response(**response))


def test_refusal_fails_closed() -> None:
    refusal = _response(
        output=[
            {
                "type": "message",
                "content": [{"type": "refusal", "refusal": "cannot comply"}],
            }
        ]
    )
    with pytest.raises(RuntimeError, match="was refused"):
        _map(refusal)


def test_http_status_check_never_includes_upstream_body() -> None:
    with pytest.raises(RuntimeError, match="HTTP request failed") as exc:
        _require_success_status(401)
    rendered = repr(exc.value)
    assert SECRET not in rendered
    assert "upstream-secret-diagnostic" not in rendered


def test_malformed_json_and_schema_fields_are_rejected() -> None:
    with pytest.raises(ValueError, match="not valid JSON"):
        _decode_json_object(b"not-json")
    with pytest.raises(ValueError, match="must be an object"):
        _decode_json_object(b"[]")

    output = _response()
    output["output"][0]["content"][0]["text"] = json.dumps(
        _structured(unexpected=["field"])
    )
    with pytest.raises(ValueError, match="fields do not match schema"):
        _map(output)


def test_token_rates_must_be_finite_and_nonnegative() -> None:
    with pytest.raises(ValueError, match="finite and non-negative"):
        OpenAIResponsesL3Provider(
            model_id=MODEL_ID,
            model_revision=MODEL_REVISION,
            cost_currency="USD",
            pricing_version="synthetic-pricing-v1",
            input_cost_per_million_tokens="NaN",
            output_cost_per_million_tokens="10",
        )


def test_contract_is_secret_free_and_synchronous() -> None:
    provider = _provider()
    rendered = repr(provider.contract)
    assert provider.provider_name == OPENAI_L3_PROVIDER
    assert provider.max_retries == 0
    assert provider.contract.status_path is None
    assert provider.contract.polling_billed is False
    assert provider.contract.production_ready is False
    assert SECRET not in rendered


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("task_key", "different-task"),
        ("prompt_version", "different-prompt"),
        ("schema_version", "different-schema"),
        ("input_fingerprint", "different-input"),
    ],
)
def test_response_binding_mismatch_is_rejected(field, value) -> None:
    output = _response()
    output["output"][0]["content"][0]["text"] = json.dumps(
        _structured(**{field: value})
    )
    with pytest.raises(ValueError, match="binding does not match"):
        _map(output)


def test_contract_source_fingerprint_is_reproducible() -> None:
    source = Path("docs/provider_contracts/openai_responses_l3_v1.json")
    assert hashlib.sha256(source.read_bytes()).hexdigest() == OPENAI_L3_DOC_FINGERPRINT


@pytest.mark.parametrize(
    "modalities",
    [
        ["metadata", "metadata"],
        ["metadata", "unsupported"],
        ["metadata"],
    ],
)
def test_invalid_or_unsupplied_response_modalities_are_rejected(modalities) -> None:
    output = _response()
    output["output"][0]["content"][0]["text"] = json.dumps(
        _structured(evidence_modalities=modalities)
    )
    with pytest.raises(ValueError, match="modalit"):
        _map(output)
