"""Fail-closed Volcengine Ark Chat Completions adapter for L3 research.

The Ark API uses an OpenAI-compatible Chat Completions envelope.  Keeping the
wire mapping here (rather than in a Windmill script) makes its request/response
binding independently testable.  This module intentionally has no transport
operation: the execution contract is permanently non-production until a later
source review explicitly changes that boundary.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal
from typing import Any

import httpx

from douyin_research.l2.transcripts import TaskCost
from douyin_research.l3.execution import L3ProviderRequest, L3ProviderResponse
from douyin_research.l3.results import (
    EVIDENCE_MODALITIES,
    MAX_ITEM_CHARS,
    MAX_SECTION_ITEMS,
    L3ResearchResult,
)

from .execution_contracts import (
    EXECUTION_CONTRACT_VERSION,
    L3_SYNC_CAPABILITY,
    VerifiedExecutionContract,
    validate_execution_contract,
)

VOLCENGINE_ARK_L3_PROVIDER = "volcengine-ark-chat-completions"
VOLCENGINE_ARK_BASE_URL = "https://ark.cn-beijing.volces.com"
VOLCENGINE_ARK_SUBMIT_PATH = "/api/v3/chat/completions"
VOLCENGINE_ARK_REQUEST_SCHEMA_VERSION = "ark-chat-completions-l3-request-v1-2026-09-20"
VOLCENGINE_ARK_RESPONSE_SCHEMA_VERSION = "ark-chat-completions-l3-response-v1-2026-09-20"
# SHA-256 of the credential-free official API contract note in
# docs/VOLCENGINE_ARK_L3_V1.md.  It is an audit marker, not an account claim.
VOLCENGINE_ARK_DOC_FINGERPRINT = "e9132f22f78e13d307bdd3d40d1bdcf4ed0a04596b0f0a592341e393b130d1f1"
# Chosen for L3's Chinese research/summarisation workload: Lite is the
# cost/latency default; an operator supplies a versioned Ark endpoint id.
RECOMMENDED_ARK_MODEL_FAMILY = "doubao-seed-2-0-lite"
_SECTIONS = (
    "narrative_structure", "hook_functions", "comment_semantics",
    "case_comparisons", "mechanism_hypotheses", "ip_fit", "limitations",
)


class VolcengineArkL3Provider:
    """Translate Ark's strict JSON-schema result to the neutral L3 contract."""

    provider_name = VOLCENGINE_ARK_L3_PROVIDER
    max_retries = 0

    def __init__(
        self,
        *,
        endpoint_id: str,
        model_id: str = RECOMMENDED_ARK_MODEL_FAMILY,
        model_revision: str,
        cost_currency: str,
        pricing_version: str,
        input_cost_per_million_tokens: Decimal | str | int,
        output_cost_per_million_tokens: Decimal | str | int,
        timeout_seconds: int = 60,
    ) -> None:
        for name, value in {
            "endpoint_id": endpoint_id, "model_id": model_id,
            "model_revision": model_revision, "cost_currency": cost_currency,
            "pricing_version": pricing_version,
        }.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Volcengine Ark L3 {name} is required")
        if timeout_seconds <= 0:
            raise ValueError("Volcengine Ark L3 timeout must be positive")
        self._endpoint_id = endpoint_id
        self._model_id = model_id
        self._model_revision = model_revision
        self._cost_currency = cost_currency
        self._pricing_version = pricing_version
        self._input_rate = _rate(input_cost_per_million_tokens, "input token rate")
        self._output_rate = _rate(output_cost_per_million_tokens, "output token rate")
        self.contract = VerifiedExecutionContract(
            provider=self.provider_name, capability=L3_SYNC_CAPABILITY,
            contract_version=EXECUTION_CONTRACT_VERSION, model_id=model_id,
            model_revision=model_revision, base_url=VOLCENGINE_ARK_BASE_URL,
            auth_scheme="bearer", auth_header_name="Authorization",
            submit_path=VOLCENGINE_ARK_SUBMIT_PATH, status_path=None,
            request_schema_version=VOLCENGINE_ARK_REQUEST_SCHEMA_VERSION,
            response_schema_version=VOLCENGINE_ARK_RESPONSE_SCHEMA_VERSION,
            cost_currency=cost_currency, polling_billed=False, max_retries=0,
            timeout_seconds=timeout_seconds,
            verified_source_fingerprint=VOLCENGINE_ARK_DOC_FINGERPRINT,
            production_ready=False,
        )

    def __repr__(self) -> str:
        return ("VolcengineArkL3Provider("
                f"model_id={self._model_id!r}, model_revision={self._model_revision!r}, "
                f"production_ready={self.contract.production_ready!r})")

    @property
    def pricing_version(self) -> str:
        return self._pricing_version

    def generate(self, request: L3ProviderRequest) -> L3ProviderResponse:
        """Fail before transport, budget spending, or any account interaction."""
        self._assert_ready(request)
        raise AssertionError("unreachable while production_ready is false")

    def _assert_ready(self, request: L3ProviderRequest) -> None:
        self._assert_request(request)
        validate_execution_contract(
            self.contract, expected_provider=self.provider_name,
            expected_capability=L3_SYNC_CAPABILITY, expected_model_id=self._model_id,
            expected_model_revision=self._model_revision,
            expected_currency=self._cost_currency,
        )

    def _assert_request(self, request: L3ProviderRequest) -> None:
        if (request.model_id, request.model_revision) != (self._model_id, self._model_revision):
            raise ValueError("Volcengine Ark L3 request model does not match adapter")
        review = request.evidence_bundle.get("privacy_review")
        if not isinstance(review, Mapping) or review.get("reviewed") is not True:
            raise ValueError("Volcengine Ark L3 evidence privacy review is not complete")
        if not isinstance(review.get("version"), str) or not review["version"].strip():
            raise ValueError("Volcengine Ark L3 privacy review version is required")
        _request_modalities(request)


class VerifiedLiveVolcengineArkL3Provider(VolcengineArkL3Provider):
    """One-shot Ark transport with a source-reviewed, fixed live boundary.

    This class is deliberately separate from :class:`VolcengineArkL3Provider`.
    Instantiating the default provider can never turn on network access; this
    class is the explicit code-reviewed integration point.  It has no
    ``production_ready`` argument and never retries a request.

    ``expected_response_model`` is deliberately distinct from ``model_revision``:
    Ark may put either the endpoint id or a provider-side serving revision into
    its OpenAI-compatible response envelope.  The caller must record the value
    it expects for the selected endpoint rather than silently accepting either.
    """

    def __init__(
        self,
        *,
        api_key: str,
        expected_response_model: str,
        client: httpx.Client | None = None,
        **kwargs: Any,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("Volcengine Ark L3 API key is required")
        if not isinstance(expected_response_model, str) or not expected_response_model.strip():
            raise ValueError("Volcengine Ark L3 expected response model is required")
        super().__init__(**kwargs)
        self._expected_response_model = expected_response_model
        if client is not None:
            expected_base = httpx.URL(VOLCENGINE_ARK_BASE_URL)
            if client.base_url != expected_base:
                raise ValueError("Volcengine Ark L3 client base URL is not allowed")
            if client.follow_redirects:
                raise ValueError("Volcengine Ark L3 client redirects must be disabled")
        # This is a source-level choice of the reviewed class, not a caller
        # switch.  The default class above remains permanently fail-closed.
        self.contract = replace(self.contract, production_ready=True)
        self._api_key = api_key
        self._client = client or httpx.Client(
            base_url=VOLCENGINE_ARK_BASE_URL,
            timeout=self.contract.timeout_seconds,
            follow_redirects=False,
        )

    def __repr__(self) -> str:
        return (
            "VerifiedLiveVolcengineArkL3Provider("
            f"model_id={self._model_id!r}, model_revision={self._model_revision!r}, "
            f"expected_response_model={self._expected_response_model!r}, "
            "production_ready=True)"
        )

    def close(self) -> None:
        """Close the internally supplied or injected HTTP client."""
        self._client.close()

    def generate(self, request: L3ProviderRequest) -> L3ProviderResponse:
        self._assert_ready(request)
        try:
            response = self._client.post(
                VOLCENGINE_ARK_SUBMIT_PATH,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json=ark_request_body(self, request),
            )
        except httpx.HTTPError:
            raise RuntimeError("Volcengine Ark L3 request failed") from None
        if response.status_code < 200 or response.status_code >= 300:
            # Do not include a provider body: it can contain prompt fragments,
            # request ids, account information, or echoed credentials.
            raise RuntimeError(
                f"Volcengine Ark L3 returned HTTP {response.status_code}"
            )
        payload = _json_object(response)
        return map_ark_completed_response(
            payload,
            request,
            expected_response_model=self._expected_response_model,
            cost_currency=self._cost_currency,
            input_rate=self._input_rate,
            output_rate=self._output_rate,
        )


def ark_request_body(provider: VolcengineArkL3Provider, request: L3ProviderRequest) -> dict[str, object]:
    """Build a credential-free, one-shot Ark request.  Never sends it."""
    provider._assert_request(request)
    envelope = {
        "task_key": request.task_key, "prompt_version": request.prompt_version,
        "schema_version": request.schema_version, "input_fingerprint": request.input_fingerprint,
        "evidence": request.evidence_bundle,
    }
    return {
        "model": provider._endpoint_id,
        "temperature": 0,
        "stream": False,
        "messages": [
            {"role": "system", "content": "Analyze only supplied privacy-reviewed evidence. Return strict JSON; state uncertainty in limitations."},
            {"role": "user", "content": json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))},
        ],
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "l3_research_result", "strict": True, "schema": _result_schema(),
        }},
    }


def map_ark_completed_response(
    payload: Mapping[str, Any],
    request: L3ProviderRequest,
    *,
    expected_response_model: str | None = None,
    model_revision: str | None = None,
    cost_currency: str,
    input_rate: Decimal,
    output_rate: Decimal,
) -> L3ProviderResponse:
    """Validate a completed Ark Chat Completions payload, including binding."""
    if expected_response_model is None:
        # Backward-compatible for offline callers.  All live callers must pass
        # the explicit endpoint-specific response value.
        expected_response_model = model_revision
    if not isinstance(expected_response_model, str) or not expected_response_model.strip():
        raise ValueError("Volcengine Ark L3 expected response model is required")
    if payload.get("model") != expected_response_model:
        raise ValueError("Volcengine Ark L3 response model does not match expected response model")
    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], Mapping):
        raise ValueError("Volcengine Ark L3 response must contain exactly one choice")
    message = choices[0].get("message")
    if not isinstance(message, Mapping):
        raise ValueError("Volcengine Ark L3 response message is missing")
    if message.get("refusal"):
        raise RuntimeError("Volcengine Ark L3 response was refused")
    content = message.get("content")
    if not isinstance(content, str):
        raise ValueError("Volcengine Ark L3 response content must be a string")
    try:
        structured = json.loads(content)
    except json.JSONDecodeError:
        raise ValueError("Volcengine Ark L3 structured output is not valid JSON") from None
    if not isinstance(structured, Mapping) or set(structured) != _schema_keys():
        raise ValueError("Volcengine Ark L3 structured output fields do not match schema")
    if tuple(structured.get(k) for k in ("task_key", "prompt_version", "schema_version", "input_fingerprint")) != (request.task_key, request.prompt_version, request.schema_version, request.input_fingerprint):
        raise ValueError("Volcengine Ark L3 structured output binding does not match request")
    modalities = _modalities(structured)
    if set(modalities) != set(_request_modalities(request)):
        raise ValueError("Volcengine Ark L3 evidence modalities do not match input")
    usage = payload.get("usage")
    if not isinstance(usage, Mapping):
        raise ValueError("Volcengine Ark L3 usage must be an object")
    tokens = (usage.get("prompt_tokens"), usage.get("completion_tokens"))
    if any(type(v) is not int or v < 0 for v in tokens):
        raise ValueError("Volcengine Ark L3 token usage is missing or invalid")
    result = L3ResearchResult(
        model_id=request.model_id, model_revision=request.model_revision,
        prompt_version=request.prompt_version, schema_version=request.schema_version,
        input_fingerprint=request.input_fingerprint, evidence_modalities=modalities,
        **{name: _strings(structured, name) for name in _SECTIONS}, privacy_reviewed=True,
    )
    cost = (Decimal(tokens[0]) * input_rate + Decimal(tokens[1]) * output_rate) / Decimal(1_000_000)
    return L3ProviderResponse(result=result, cost=TaskCost(api_cost=Decimal("0"), asr_cost=Decimal("0"), llm_cost=cost, currency=cost_currency, basis="estimated"))


def _result_schema() -> dict[str, object]:
    properties = {k: {"type": "string", "minLength": 1, "maxLength": 200} for k in ("task_key", "prompt_version", "schema_version", "input_fingerprint")}
    properties["evidence_modalities"] = _array_schema()
    properties.update({k: _array_schema() for k in _SECTIONS})
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _array_schema() -> dict[str, object]:
    return {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": MAX_ITEM_CHARS}, "minItems": 1, "maxItems": MAX_SECTION_ITEMS}


def _schema_keys() -> set[str]:
    return {"task_key", "prompt_version", "schema_version", "input_fingerprint", "evidence_modalities", *_SECTIONS}


def _strings(payload: Mapping[str, Any], name: str) -> tuple[str, ...]:
    value = payload.get(name)
    if not isinstance(value, list) or not value or len(value) > MAX_SECTION_ITEMS or any(not isinstance(x, str) or not x.strip() or len(x) > MAX_ITEM_CHARS for x in value):
        raise ValueError(f"Volcengine Ark L3 field is invalid: {name}")
    return tuple(value)


def _modalities(payload: Mapping[str, Any]) -> tuple[str, ...]:
    value = _strings(payload, "evidence_modalities")
    if len(value) != len(set(value)) or set(value) - EVIDENCE_MODALITIES:
        raise ValueError("Volcengine Ark L3 evidence modality is invalid")
    return value


def _request_modalities(request: L3ProviderRequest) -> tuple[str, ...]:
    value = request.evidence_bundle.get("modalities")
    if not isinstance(value, list) or not value or any(not isinstance(x, str) or not x for x in value) or len(value) != len(set(value)) or set(value) - EVIDENCE_MODALITIES:
        raise ValueError("Volcengine Ark L3 input evidence modalities are invalid")
    return tuple(value)


def _rate(value: Decimal | str | int, name: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except Exception:
        raise ValueError(f"Volcengine Ark L3 {name} must be a decimal") from None
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(f"Volcengine Ark L3 {name} must be finite and non-negative")
    return parsed


def _json_object(response: httpx.Response) -> Mapping[str, Any]:
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError):
        raise ValueError("Volcengine Ark L3 response is not valid JSON") from None
    if not isinstance(payload, Mapping):
        raise ValueError("Volcengine Ark L3 response JSON must be an object")
    return payload
