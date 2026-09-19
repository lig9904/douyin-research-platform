from __future__ import annotations

import json
from decimal import Decimal

import pytest

from douyin_research.l3.execution import L3ProviderRequest
from douyin_research.providers.volcengine_ark_l3 import (
    RECOMMENDED_ARK_MODEL_FAMILY,
    VolcengineArkL3Provider,
    ark_request_body,
    map_ark_completed_response,
)


def request(**overrides) -> L3ProviderRequest:
    values = {"task_key": "task-1", "model_id": RECOMMENDED_ARK_MODEL_FAMILY,
              "model_revision": "revision-1", "prompt_version": "prompt-1",
              "schema_version": "schema-1", "input_fingerprint": "fingerprint-1",
              "evidence_bundle": {"modalities": ["metadata", "comments"], "privacy_review": {"reviewed": True, "version": "privacy-v1"}}}
    values.update(overrides)
    return L3ProviderRequest(**values)


def provider(**overrides) -> VolcengineArkL3Provider:
    values = {"endpoint_id": "ep-opaque-not-a-secret", "model_revision": "revision-1",
              "cost_currency": "CNY", "pricing_version": "price-v1",
              "input_cost_per_million_tokens": "1", "output_cost_per_million_tokens": "2"}
    values.update(overrides)
    return VolcengineArkL3Provider(**values)


def structured(req: L3ProviderRequest) -> dict[str, object]:
    values = {"task_key": req.task_key, "prompt_version": req.prompt_version,
              "schema_version": req.schema_version, "input_fingerprint": req.input_fingerprint,
              "evidence_modalities": ["metadata", "comments"]}
    values.update({name: ["受限结论"] for name in ("narrative_structure", "hook_functions", "comment_semantics", "case_comparisons", "mechanism_hypotheses", "ip_fit", "limitations")})
    return values


def payload(req: L3ProviderRequest) -> dict[str, object]:
    return {"model": "revision-1", "choices": [{"message": {"content": json.dumps(structured(req))}}], "usage": {"prompt_tokens": 100, "completion_tokens": 50}}


def test_body_is_strict_non_streaming_and_never_contains_credentials() -> None:
    body = ark_request_body(provider(), request())
    assert body["model"] == "ep-opaque-not-a-secret"
    assert body["temperature"] == 0 and body["stream"] is False
    assert body["response_format"]["json_schema"]["strict"] is True
    assert "Authorization" not in repr(body) and "api_key" not in repr(body).lower()


def test_generate_is_permanently_fail_closed() -> None:
    p = provider()
    assert p.contract.production_ready is False and p.max_retries == 0
    with pytest.raises(ValueError, match="not production ready"):
        p.generate(request())
    with pytest.raises(TypeError, match="production_ready"):
        VolcengineArkL3Provider(endpoint_id="x", model_revision="r", cost_currency="CNY", pricing_version="p", input_cost_per_million_tokens=0, output_cost_per_million_tokens=0, production_ready=True)


def test_response_mapping_binds_every_request_field_and_cost() -> None:
    result = map_ark_completed_response(payload(request()), request(), model_revision="revision-1", cost_currency="CNY", input_rate=1, output_rate=2)
    assert result.result.input_fingerprint == "fingerprint-1"
    assert result.cost.llm_cost == Decimal("0.0002")


@pytest.mark.parametrize("change", ["task_key", "prompt_version", "schema_version", "input_fingerprint"])
def test_response_binding_mismatch_is_rejected(change: str) -> None:
    raw = payload(request())
    content = json.loads(raw["choices"][0]["message"]["content"])
    content[change] = "wrong"
    raw["choices"][0]["message"]["content"] = json.dumps(content)
    with pytest.raises(ValueError, match="binding"):
        map_ark_completed_response(raw, request(), model_revision="revision-1", cost_currency="CNY", input_rate=1, output_rate=2)


def test_refusal_unknown_modality_and_missing_usage_are_rejected() -> None:
    refused = payload(request())
    refused["choices"][0]["message"] = {"refusal": "no"}
    with pytest.raises(RuntimeError, match="refused"):
        map_ark_completed_response(refused, request(), model_revision="revision-1", cost_currency="CNY", input_rate=1, output_rate=2)
    unknown = payload(request())
    content = json.loads(unknown["choices"][0]["message"]["content"])
    content["evidence_modalities"] = ["unknown"]
    unknown["choices"][0]["message"]["content"] = json.dumps(content)
    with pytest.raises(ValueError, match="modality"):
        map_ark_completed_response(unknown, request(), model_revision="revision-1", cost_currency="CNY", input_rate=1, output_rate=2)
