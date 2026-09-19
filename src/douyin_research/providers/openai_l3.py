"""Zero-retry OpenAI Responses API adapter for structured L3 research."""

from __future__ import annotations

import json
from collections.abc import Mapping
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


OPENAI_L3_PROVIDER = "openai-responses"
OPENAI_L3_BASE_URL = "https://api.openai.com"
OPENAI_L3_SUBMIT_PATH = "/v1/responses"
OPENAI_L3_REQUEST_SCHEMA_VERSION = "openai-responses-v1-l3-request-2026-09-19"
OPENAI_L3_RESPONSE_SCHEMA_VERSION = "openai-responses-v1-l3-response-2026-09-19"
# SHA-256 of the reviewed, credential-free API contract notes. This is an audit
# marker, not a credential and not a claim that account-side billing is verified.
OPENAI_L3_DOC_FINGERPRINT = (
    "a2a8940754bec9a43d9afdd935a9bfece87cb20ae7d72123ca3eababb308b4fa"
)
_SECTIONS = (
    "narrative_structure",
    "hook_functions",
    "comment_semantics",
    "case_comparisons",
    "mechanism_hypotheses",
    "ip_fit",
    "limitations",
)


class OpenAIResponsesL3Provider:
    """Map one strict Responses API call into the provider-neutral L3 result."""

    provider_name = OPENAI_L3_PROVIDER
    max_retries = 0

    def __init__(
        self,
        *,
        api_key: str,
        model_id: str,
        model_revision: str,
        cost_currency: str,
        pricing_version: str,
        input_cost_per_million_tokens: Decimal | str | int,
        output_cost_per_million_tokens: Decimal | str | int,
        timeout_seconds: int = 60,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("OpenAI API key is required")
        for name, value in {
            "model_id": model_id,
            "model_revision": model_revision,
            "cost_currency": cost_currency,
            "pricing_version": pricing_version,
        }.items():
            if not value.strip():
                raise ValueError(f"OpenAI L3 {name} is required")
        self._api_key = api_key
        self._model_id = model_id
        self._model_revision = model_revision
        self._cost_currency = cost_currency
        self._pricing_version = pricing_version
        self._input_rate = _nonnegative_decimal(
            input_cost_per_million_tokens, "input token rate"
        )
        self._output_rate = _nonnegative_decimal(
            output_cost_per_million_tokens, "output token rate"
        )
        self._client = client or httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=False,
        )
        self._owns_client = client is None
        self.contract = VerifiedExecutionContract(
            provider=self.provider_name,
            capability=L3_SYNC_CAPABILITY,
            contract_version=EXECUTION_CONTRACT_VERSION,
            model_id=model_id,
            model_revision=model_revision,
            base_url=OPENAI_L3_BASE_URL,
            auth_scheme="bearer",
            auth_header_name="Authorization",
            submit_path=OPENAI_L3_SUBMIT_PATH,
            status_path=None,
            request_schema_version=OPENAI_L3_REQUEST_SCHEMA_VERSION,
            response_schema_version=OPENAI_L3_RESPONSE_SCHEMA_VERSION,
            cost_currency=cost_currency,
            polling_billed=False,
            max_retries=0,
            timeout_seconds=timeout_seconds,
            verified_source_fingerprint=OPENAI_L3_DOC_FINGERPRINT,
            production_ready=False,
        )

    def __repr__(self) -> str:
        return (
            "OpenAIResponsesL3Provider("
            f"model_id={self._model_id!r}, model_revision={self._model_revision!r}, "
            f"production_ready={self.contract.production_ready!r})"
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def generate(self, request: L3ProviderRequest) -> L3ProviderResponse:
        self._assert_ready(request)
        return self._generate_with_transport(request)

    def _generate_with_transport(
        self, request: L3ProviderRequest
    ) -> L3ProviderResponse:
        """Exercise the wire contract in offline tests after local validation.

        Production code must call ``generate``. This private seam cannot make a
        default adapter production-ready and exists only to test MockTransport
        request/response behavior while the public contract remains blocked.
        """

        self._assert_request(request)
        try:
            response = self._client.post(
                OPENAI_L3_BASE_URL + OPENAI_L3_SUBMIT_PATH,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=_request_body(request),
            )
            response.raise_for_status()
        except httpx.HTTPError:
            raise RuntimeError("OpenAI L3 HTTP request failed") from None

        payload = _json_object(response)
        if payload.get("status") != "completed":
            raise RuntimeError("OpenAI L3 response did not complete")
        if payload.get("model") != self._model_revision:
            raise ValueError("OpenAI L3 response model revision does not match request")
        structured = _structured_output(payload)
        _assert_response_binding(structured, request)
        usage = _usage(payload)
        result = L3ResearchResult(
            model_id=request.model_id,
            model_revision=request.model_revision,
            prompt_version=request.prompt_version,
            schema_version=request.schema_version,
            input_fingerprint=request.input_fingerprint,
            evidence_modalities=_evidence_modalities(structured),
            **{name: _string_tuple(structured, name) for name in _SECTIONS},
            privacy_reviewed=True,
        )
        token_cost = (
            Decimal(usage[0]) * self._input_rate
            + Decimal(usage[1]) * self._output_rate
        ) / Decimal(1_000_000)
        return L3ProviderResponse(
            result=result,
            cost=TaskCost(
                api_cost=Decimal("0"),
                asr_cost=Decimal("0"),
                llm_cost=token_cost,
                currency=self._cost_currency,
                basis="estimated",
            ),
        )

    def _assert_ready(self, request: L3ProviderRequest) -> None:
        self._assert_request(request)
        validate_execution_contract(
            self.contract,
            expected_provider=self.provider_name,
            expected_capability=L3_SYNC_CAPABILITY,
            expected_model_id=self._model_id,
            expected_model_revision=self._model_revision,
            expected_currency=self._cost_currency,
        )

    def _assert_request(self, request: L3ProviderRequest) -> None:
        expected = (self._model_id, self._model_revision)
        if (request.model_id, request.model_revision) != expected:
            raise ValueError("OpenAI L3 request model does not match adapter")
        review = request.evidence_bundle.get("privacy_review")
        if not isinstance(review, Mapping):
            raise ValueError("OpenAI L3 evidence requires a privacy review")
        if review.get("reviewed") is not True:
            raise ValueError("OpenAI L3 evidence privacy review is not complete")
        version = review.get("version")
        if not isinstance(version, str) or not version.strip():
            raise ValueError("OpenAI L3 privacy review version is required")


def _request_body(request: L3ProviderRequest) -> dict[str, object]:
    envelope = {
        "task_key": request.task_key,
        "prompt_version": request.prompt_version,
        "schema_version": request.schema_version,
        "input_fingerprint": request.input_fingerprint,
        "evidence": request.evidence_bundle,
    }
    return {
        "model": request.model_revision,
        "store": False,
        "instructions": (
            "Analyze only the supplied privacy-reviewed evidence. Return the "
            "strict JSON schema; preserve uncertainty in limitations. "
            f"Prompt version: {request.prompt_version}."
        ),
        "input": json.dumps(
            envelope,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        "text": {
            "format": {
                "type": "json_schema",
                "name": "l3_research_result",
                "strict": True,
                "schema": _result_schema(),
            }
        },
    }


def _result_schema() -> dict[str, object]:
    properties: dict[str, object] = {
        "task_key": {"type": "string", "minLength": 1, "maxLength": 200},
        "prompt_version": {"type": "string", "minLength": 1},
        "schema_version": {"type": "string", "minLength": 1},
        "input_fingerprint": {"type": "string", "minLength": 1},
        "evidence_modalities": _string_array_schema(),
        **{name: _string_array_schema() for name in _SECTIONS},
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _string_array_schema() -> dict[str, object]:
    return {
        "type": "array",
        "items": {"type": "string", "minLength": 1, "maxLength": 500},
        "minItems": 1,
        "maxItems": 12,
    }


def _json_object(response: httpx.Response) -> Mapping[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        raise ValueError("OpenAI L3 response body is not valid JSON") from None
    if not isinstance(payload, Mapping):
        raise ValueError("OpenAI L3 response body must be an object")
    return payload


def _structured_output(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    output = payload.get("output")
    if not isinstance(output, list):
        raise ValueError("OpenAI L3 output must be a list")
    texts: list[str] = []
    for item in output:
        if not isinstance(item, Mapping) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            raise ValueError("OpenAI L3 message content must be a list")
        for part in content:
            if isinstance(part, Mapping) and part.get("type") == "refusal":
                raise RuntimeError("OpenAI L3 response was refused")
            if isinstance(part, Mapping) and part.get("type") == "output_text":
                text = part.get("text")
                if not isinstance(text, str):
                    raise ValueError("OpenAI L3 output text must be a string")
                texts.append(text)
    if len(texts) != 1:
        raise ValueError("OpenAI L3 response must contain exactly one output text")
    try:
        value = json.loads(texts[0])
    except json.JSONDecodeError:
        raise ValueError("OpenAI L3 structured output is not valid JSON") from None
    if not isinstance(value, Mapping):
        raise ValueError("OpenAI L3 structured output must be an object")
    allowed = {
        "task_key",
        "prompt_version",
        "schema_version",
        "input_fingerprint",
        "evidence_modalities",
        *_SECTIONS,
    }
    if set(value) != allowed:
        raise ValueError("OpenAI L3 structured output fields do not match schema")
    return value


def _usage(payload: Mapping[str, Any]) -> tuple[int, int]:
    usage = payload.get("usage")
    if not isinstance(usage, Mapping):
        raise ValueError("OpenAI L3 usage must be an object")
    values = (usage.get("input_tokens"), usage.get("output_tokens"))
    if any(type(value) is not int or value < 0 for value in values):
        raise ValueError("OpenAI L3 token usage is missing or invalid")
    return values  # type: ignore[return-value]


def _string_tuple(payload: Mapping[str, Any], name: str) -> tuple[str, ...]:
    value = payload.get(name)
    if not isinstance(value, list) or not value:
        raise ValueError(f"OpenAI L3 field must be a non-empty list: {name}")
    if any(not isinstance(item, str) for item in value):
        raise ValueError(f"OpenAI L3 field must contain strings: {name}")
    if len(value) > MAX_SECTION_ITEMS:
        raise ValueError(f"OpenAI L3 field exceeds item limit: {name}")
    if any(not item.strip() or len(item) > MAX_ITEM_CHARS for item in value):
        raise ValueError(f"OpenAI L3 field contains an invalid string: {name}")
    return tuple(value)


def _evidence_modalities(payload: Mapping[str, Any]) -> tuple[str, ...]:
    modalities = _string_tuple(payload, "evidence_modalities")
    if len(set(modalities)) != len(modalities):
        raise ValueError("OpenAI L3 evidence modalities must be unique")
    if set(modalities) - EVIDENCE_MODALITIES:
        raise ValueError("OpenAI L3 evidence modality is unsupported")
    return modalities


def _assert_response_binding(
    payload: Mapping[str, Any], request: L3ProviderRequest
) -> None:
    actual = tuple(
        payload.get(name)
        for name in (
            "task_key",
            "prompt_version",
            "schema_version",
            "input_fingerprint",
        )
    )
    expected = (
        request.task_key,
        request.prompt_version,
        request.schema_version,
        request.input_fingerprint,
    )
    if actual != expected:
        raise ValueError("OpenAI L3 structured output binding does not match request")


def _nonnegative_decimal(value: Decimal | str | int, name: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except Exception:
        raise ValueError(f"OpenAI L3 {name} must be a decimal") from None
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(f"OpenAI L3 {name} must be finite and non-negative")
    return parsed
