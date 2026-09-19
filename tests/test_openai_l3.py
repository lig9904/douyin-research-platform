from __future__ import annotations

import json
import hashlib
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from douyin_research.l3 import L3ProviderRequest, L3_SCHEMA_VERSION
from douyin_research.providers.openai_l3 import (
    OPENAI_L3_DOC_FINGERPRINT,
    OPENAI_L3_PROVIDER,
    OpenAIResponsesL3Provider,
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
            "evidence_modalities": ["metadata", "comments"],
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


def _provider(handler):
    return OpenAIResponsesL3Provider(
        api_key=SECRET,
        model_id=MODEL_ID,
        model_revision=MODEL_REVISION,
        cost_currency="USD",
        pricing_version="synthetic-pricing-v1",
        input_cost_per_million_tokens="2.50",
        output_cost_per_million_tokens="10.00",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_adapter_defaults_to_fail_closed_without_network() -> None:
    calls = []
    provider = _provider(lambda request: calls.append(request))

    with pytest.raises(ValueError, match="not production ready"):
        provider.generate(_request())

    assert calls == []
    assert provider.contract.production_ready is False
    assert SECRET not in repr(provider)


def test_production_ready_cannot_be_enabled_by_constructor() -> None:
    with pytest.raises(TypeError, match="production_ready"):
        OpenAIResponsesL3Provider(
            api_key=SECRET,
            model_id=MODEL_ID,
            model_revision=MODEL_REVISION,
            cost_currency="USD",
            pricing_version="synthetic-pricing-v1",
            input_cost_per_million_tokens="2.50",
            output_cost_per_million_tokens="10.00",
            production_ready=True,
        )


def test_generate_sends_one_strict_non_stored_request_and_maps_result() -> None:
    calls = []

    def handler(request):
        calls.append(request)
        body = json.loads(request.content)
        assert request.headers["Authorization"] == f"Bearer {SECRET}"
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

    result = _provider(handler)._generate_with_transport(_request())

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


def test_model_mismatch_fails_before_network() -> None:
    calls = []
    provider = _provider(lambda request: calls.append(request))
    with pytest.raises(ValueError, match="model does not match"):
        provider.generate(_request(model_revision="different-revision"))
    assert calls == []


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
    payload = _response(**response)
    provider = _provider(lambda request: httpx.Response(200, json=payload))
    with pytest.raises((ValueError, RuntimeError), match=message):
        provider._generate_with_transport(_request())


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
        provider = _provider(lambda request: httpx.Response(200, json=refusal))
        provider._generate_with_transport(_request())


def test_secret_and_upstream_body_are_hidden_on_http_error() -> None:
    provider = _provider(
        lambda request: httpx.Response(
            401, json={"error": {"message": "upstream-secret-diagnostic"}}
        )
    )
    with pytest.raises(RuntimeError, match="HTTP request failed") as exc:
        provider._generate_with_transport(_request())
    rendered = repr(exc.value)
    assert SECRET not in rendered
    assert "upstream-secret-diagnostic" not in rendered


def test_malformed_json_and_schema_fields_are_rejected() -> None:
    provider = _provider(lambda request: httpx.Response(200, content=b"not-json"))
    with pytest.raises(ValueError, match="not valid JSON"):
        provider._generate_with_transport(_request())

    output = _response()
    value = _structured(unexpected=["field"])
    output["output"][0]["content"][0]["text"] = json.dumps(value)
    with pytest.raises(ValueError, match="fields do not match schema"):
        provider = _provider(lambda request: httpx.Response(200, json=output))
        provider._generate_with_transport(_request())


def test_token_rates_must_be_finite_and_nonnegative() -> None:
    with pytest.raises(ValueError, match="finite and non-negative"):
        OpenAIResponsesL3Provider(
            api_key=SECRET,
            model_id=MODEL_ID,
            model_revision=MODEL_REVISION,
            cost_currency="USD",
            pricing_version="synthetic-pricing-v1",
            input_cost_per_million_tokens="NaN",
            output_cost_per_million_tokens="10",
        )


def test_contract_is_secret_free_and_synchronous() -> None:
    provider = _provider(lambda request: httpx.Response(200, json=_response()))
    rendered = repr(provider.contract)
    assert provider.provider_name == OPENAI_L3_PROVIDER
    assert provider.max_retries == 0
    assert provider.contract.status_path is None
    assert provider.contract.polling_billed is False
    assert provider.contract.production_ready is False
    assert SECRET not in rendered


def test_unreviewed_evidence_fails_before_network() -> None:
    calls = []
    provider = _provider(lambda request: calls.append(request))
    evidence = {"privacy_review": {"reviewed": False, "version": "privacy-v1"}}
    with pytest.raises(ValueError, match="privacy review is not complete"):
        provider.generate(_request(evidence_bundle=evidence))
    assert calls == []


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
    structured = _structured(**{field: value})
    output["output"][0]["content"][0]["text"] = json.dumps(structured)
    provider = _provider(lambda request: httpx.Response(200, json=output))
    with pytest.raises(ValueError, match="binding does not match"):
        provider._generate_with_transport(_request())


def test_contract_source_fingerprint_is_reproducible() -> None:
    source = Path("docs/provider_contracts/openai_responses_l3_v1.json")
    assert hashlib.sha256(source.read_bytes()).hexdigest() == OPENAI_L3_DOC_FINGERPRINT


@pytest.mark.parametrize(
    "modalities",
    [["metadata", "metadata"], ["metadata", "unsupported"]],
)
def test_invalid_evidence_modalities_are_rejected(modalities) -> None:
    output = _response()
    output["output"][0]["content"][0]["text"] = json.dumps(
        _structured(evidence_modalities=modalities)
    )
    provider = _provider(lambda request: httpx.Response(200, json=output))
    with pytest.raises(ValueError, match="modalit"):
        provider._generate_with_transport(_request())
