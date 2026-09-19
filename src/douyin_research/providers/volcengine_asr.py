"""Zero-retry adapter for Volcengine Doubao asynchronous file ASR."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from typing import Any, Callable
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

import httpx

from douyin_research.l2.asr_execution import (
    ASRProviderRequest,
    ASRProviderState,
)
from douyin_research.l2.transcripts import (
    TaskCost,
    TranscriptEvidence,
    TranscriptSegment,
)

from .execution_contracts import (
    ASR_ASYNC_CAPABILITY,
    EXECUTION_CONTRACT_VERSION,
    VerifiedExecutionContract,
    validate_execution_contract,
)

VOLCENGINE_ASR_PROVIDER = "volcengine-doubao-asr"
VOLCENGINE_ASR_MODEL_ID = "bigmodel"
VOLCENGINE_ASR_MODEL_REVISION = "2.0"
VOLCENGINE_ASR_ENGINE_VERSION = "volc.seedasr.auc"
VOLCENGINE_ASR_BASE_URL = "https://openspeech.bytedance.com"
VOLCENGINE_ASR_SUBMIT_PATH = "/api/v3/auc/bigmodel/submit"
VOLCENGINE_ASR_QUERY_PATH = "/api/v3/auc/bigmodel/query"
VOLCENGINE_ASR_DOC_FINGERPRINT = (
    "f159a0115c94a87190a7dc4c1914c8dc8175c1e6be3b4e9d065079e2a9c9d37d"
)
_SUCCESS = "20000000"
_RUNNING = frozenset({"20000001", "20000002"})
_NO_SPEECH = "20000003"
_ALLOWED_FORMATS = frozenset({"raw", "wav", "mp3", "ogg", "m4a"})
VOLCENGINE_ASR_READINESS_VERSION = "volcengine-doubao-asr-readiness-v1.0.0"


@dataclass(frozen=True, slots=True)
class VolcengineASRReadinessFacts:
    """Non-secret, operator-supplied facts used for a local readiness review.

    This deliberately accepts only booleans and version labels.  It does not
    accept an API key, account identifier, endpoint override, signed URL, or
    any other credential-bearing value.
    """

    secret_configured: bool
    model_enabled: bool
    media_delivery_verified: bool
    price_catalog_version: str | None
    cost_reconciliation_version: str | None
    polling_policy_version: str | None
    polling_billed: bool | None


@dataclass(frozen=True, slots=True)
class VolcengineASRReadinessReport:
    """Auditable local readiness result; never authorizes a paid call."""

    readiness_version: str
    provider: str
    model_id: str
    model_revision: str
    engine_version: str
    documentation_fingerprint: str
    production_ready: bool
    review_complete: bool
    blockers: tuple[str, ...]
    facts: VolcengineASRReadinessFacts
    report_fingerprint: str

    def to_dict(self) -> dict[str, object]:
        """Return only non-secret facts suitable for a review artifact."""

        return {
            "readiness_version": self.readiness_version,
            "provider": self.provider,
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "engine_version": self.engine_version,
            "documentation_fingerprint": self.documentation_fingerprint,
            "production_ready": self.production_ready,
            "review_complete": self.review_complete,
            "blockers": list(self.blockers),
            "facts": asdict(self.facts),
            "report_fingerprint": self.report_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class _VolcengineASRWireRequest:
    """Non-secret wire shape, intentionally without a transport operation."""

    path: str
    provider_task_ref: str
    headers: tuple[tuple[str, str], ...]
    payload: Mapping[str, object]


@dataclass(frozen=True, slots=True, repr=False)
class ReviewedASRMediaDelivery:
    """A narrowly reviewed delivery URL for the one live adapter.

    A normal ASR request accepts an HTTPS URL without a query string.  Signed
    URLs are deliberately different: their query can contain a bearer-like
    credential.  A caller must therefore construct this review record with an
    exact URL, an operator review version, and a SHA-256 of the *query only*.
    It cannot be enabled by passing a boolean or an arbitrary ``allow_query``
    option.  Its representation deliberately omits the URL and query.
    """

    url: str
    review_version: str
    query_sha256: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.url, str):
            raise ValueError("reviewed ASR media URL is required")
        if not isinstance(self.review_version, str) or not self.review_version.strip():
            raise ValueError("reviewed ASR media review version is required")
        parsed = _parse_credential_free_https_url(self.url)
        if parsed.query:
            if not isinstance(self.query_sha256, str) or not _is_sha256(self.query_sha256):
                raise ValueError("reviewed ASR media query requires SHA-256")
            actual = hashlib.sha256(parsed.query.encode("utf-8")).hexdigest()
            if actual != self.query_sha256:
                raise ValueError("reviewed ASR media query SHA-256 does not match")
        elif self.query_sha256 is not None:
            raise ValueError("reviewed ASR media query SHA-256 requires a query")

    def __repr__(self) -> str:
        return (
            "ReviewedASRMediaDelivery("
            f"review_version={self.review_version!r}, "
            f"has_query={bool(urlsplit(self.url).query)!r})"
        )


def assess_volcengine_asr_readiness(
    facts: VolcengineASRReadinessFacts,
) -> VolcengineASRReadinessReport:
    """Assess ASR launch prerequisites locally, without opening an HTTP client.

    A complete report is evidence for an independent production-code review,
    not a runtime switch: ``production_ready`` is intentionally always false.
    """

    if not isinstance(facts, VolcengineASRReadinessFacts):
        raise TypeError("Volcengine ASR readiness facts are required")

    blockers: list[str] = []
    for name in ("secret_configured", "model_enabled", "media_delivery_verified"):
        if type(getattr(facts, name)) is not bool:
            raise ValueError(f"Volcengine ASR readiness fact must be boolean: {name}")

    if not facts.secret_configured:
        blockers.append("secret_not_configured")
    if not facts.model_enabled:
        blockers.append("model_not_enabled")
    if not facts.media_delivery_verified:
        blockers.append("media_delivery_not_verified")

    for name in (
        "price_catalog_version",
        "cost_reconciliation_version",
        "polling_policy_version",
    ):
        value = getattr(facts, name)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"Volcengine ASR readiness version is invalid: {name}")
        if value is None:
            blockers.append(f"{name}_missing")

    if type(facts.polling_billed) is not bool:
        blockers.append("polling_billing_fact_missing")
    elif facts.polling_billed:
        blockers.append("polling_billed_not_supported")

    immutable = {
        "readiness_version": VOLCENGINE_ASR_READINESS_VERSION,
        "provider": VOLCENGINE_ASR_PROVIDER,
        "model_id": VOLCENGINE_ASR_MODEL_ID,
        "model_revision": VOLCENGINE_ASR_MODEL_REVISION,
        "engine_version": VOLCENGINE_ASR_ENGINE_VERSION,
        "documentation_fingerprint": VOLCENGINE_ASR_DOC_FINGERPRINT,
        "production_ready": False,
        "review_complete": not blockers,
        "blockers": blockers,
        "facts": asdict(facts),
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            immutable,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return VolcengineASRReadinessReport(
        readiness_version=VOLCENGINE_ASR_READINESS_VERSION,
        provider=VOLCENGINE_ASR_PROVIDER,
        model_id=VOLCENGINE_ASR_MODEL_ID,
        model_revision=VOLCENGINE_ASR_MODEL_REVISION,
        engine_version=VOLCENGINE_ASR_ENGINE_VERSION,
        documentation_fingerprint=VOLCENGINE_ASR_DOC_FINGERPRINT,
        production_ready=False,
        review_complete=not blockers,
        blockers=tuple(blockers),
        facts=facts,
        report_fingerprint=fingerprint,
    )


class VolcengineDoubaoASRProvider:
    """Map the official HTTP contract into provider-neutral ASR evidence.

    The adapter is permanently non-production. A separate code review must
    deliberately replace this boundary after the local readiness report is
    complete; callers cannot switch it on at runtime.
    """

    provider_name = VOLCENGINE_ASR_PROVIDER
    max_retries = 0

    def __init__(
        self,
        *,
        api_key: str,
        audio_format: str,
        source_fingerprint: str,
        language: str = "zh-CN",
        cost_currency: str = "CNY",
        source_provider: str | None = None,
        timeout_seconds: int = 30,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Volcengine ASR API key is required")
        if audio_format not in _ALLOWED_FORMATS:
            raise ValueError("unsupported Volcengine ASR audio format")
        if not source_fingerprint.strip():
            raise ValueError("Volcengine ASR source fingerprint is required")
        if not language.strip():
            raise ValueError("Volcengine ASR language is required")
        if not cost_currency.strip():
            raise ValueError("Volcengine ASR cost currency is required")
        self._api_key = api_key
        self._audio_format = audio_format
        self._source_fingerprint = source_fingerprint
        self._language = language
        self._cost_currency = cost_currency
        self._source_provider = source_provider
        self._client = client or httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=False,
        )
        self._owns_client = client is None
        self.contract = VerifiedExecutionContract(
            provider=self.provider_name,
            capability=ASR_ASYNC_CAPABILITY,
            contract_version=EXECUTION_CONTRACT_VERSION,
            model_id=VOLCENGINE_ASR_MODEL_ID,
            model_revision=VOLCENGINE_ASR_MODEL_REVISION,
            base_url=VOLCENGINE_ASR_BASE_URL,
            auth_scheme="header",
            auth_header_name="X-Api-Key",
            submit_path=VOLCENGINE_ASR_SUBMIT_PATH,
            status_path=VOLCENGINE_ASR_QUERY_PATH,
            request_schema_version="doubao-asr-file-v3-request-2026-06-26",
            response_schema_version="doubao-asr-file-v3-response-2026-06-26",
            cost_currency=cost_currency,
            polling_billed=False,
            max_retries=0,
            timeout_seconds=timeout_seconds,
            verified_source_fingerprint=VOLCENGINE_ASR_DOC_FINGERPRINT,
            production_ready=False,
        )

    def __repr__(self) -> str:
        return (
            "VolcengineDoubaoASRProvider("
            f"audio_format={self._audio_format!r}, "
            f"language={self._language!r}, "
            f"production_ready={self.contract.production_ready!r})"
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def submit(self, request: ASRProviderRequest) -> ASRProviderState:
        self._assert_ready(request)
        wire = self._submit_wire(request)
        response = self._post(wire)
        return _map_submit_state(
            _status_code(response),
            wire.provider_task_ref,
            unknown_cost=self._unknown_cost(),
        )

    def poll(self, provider_task_ref: str) -> ASRProviderState:
        if not provider_task_ref.strip():
            raise ValueError("Volcengine ASR provider task reference is required")
        self._assert_contract_ready()
        response = self._post(_poll_wire_request(provider_task_ref))
        return _map_poll_state(
            _status_code(response),
            provider_task_ref,
            payload=_json_object(response) if _status_code(response) == _SUCCESS else None,
            evidence_builder=self._evidence,
            cost=self._unknown_cost(),
        )

    def _assert_ready(self, request: ASRProviderRequest) -> None:
        expected = (
            request.model_id,
            request.model_revision,
            request.engine_version,
        )
        actual = (
            VOLCENGINE_ASR_MODEL_ID,
            VOLCENGINE_ASR_MODEL_REVISION,
            VOLCENGINE_ASR_ENGINE_VERSION,
        )
        if expected != actual:
            raise ValueError("Volcengine ASR request model does not match adapter")
        if request.source_fingerprint != self._source_fingerprint:
            raise ValueError("Volcengine ASR source fingerprint does not match adapter")
        self._assert_contract_ready()

    def _assert_contract_ready(self) -> None:
        validate_execution_contract(
            self.contract,
            expected_provider=self.provider_name,
            expected_capability=ASR_ASYNC_CAPABILITY,
            expected_model_id=VOLCENGINE_ASR_MODEL_ID,
            expected_model_revision=VOLCENGINE_ASR_MODEL_REVISION,
            expected_currency=self._cost_currency,
        )

    def _submit_wire(self, request: ASRProviderRequest) -> _VolcengineASRWireRequest:
        return _submit_wire_request(
            request,
            audio_format=self._audio_format,
            language=self._language,
        )

    def _post(self, wire: _VolcengineASRWireRequest) -> httpx.Response:
        self._assert_contract_ready()
        headers = dict(wire.headers)
        headers["X-Api-Key"] = self._api_key
        try:
            response = self._client.post(
                VOLCENGINE_ASR_BASE_URL + wire.path,
                headers=headers,
                json=wire.payload,
            )
            response.raise_for_status()
            return response
        except httpx.HTTPError:
            _raise_sanitized_http_failure()

    def _evidence(
        self,
        payload: Mapping[str, Any],
        *,
        quality_status: str | None = None,
    ) -> TranscriptEvidence:
        result = payload.get("result", {})
        if not isinstance(result, Mapping):
            raise ValueError("Volcengine ASR result must be an object")
        text = result.get("text", "")
        if not isinstance(text, str):
            raise ValueError("Volcengine ASR result text must be a string")

        raw_utterances = result.get("utterances", [])
        if not isinstance(raw_utterances, list):
            raise ValueError("Volcengine ASR utterances must be a list")
        segments = tuple(_segment(item) for item in raw_utterances)

        audio_info = payload.get("audio_info", {})
        duration: int | None = None
        if isinstance(audio_info, Mapping):
            raw_duration = audio_info.get("duration")
            if type(raw_duration) is int and raw_duration >= 0:
                duration = raw_duration

        resolved_quality = quality_status or ("usable" if text.strip() else "no_speech")
        return TranscriptEvidence(
            asr_provider=self.provider_name,
            model_id=VOLCENGINE_ASR_MODEL_ID,
            model_revision=VOLCENGINE_ASR_MODEL_REVISION,
            engine_version=VOLCENGINE_ASR_ENGINE_VERSION,
            source_fingerprint=self._source_fingerprint,
            text=text,
            segments=segments,
            language=self._language,
            source_provider=self._source_provider,
            audio_duration_ms=duration,
            quality_status=resolved_quality,
        )

    def _unknown_cost(self) -> TaskCost:
        return TaskCost(
            api_cost=None,
            asr_cost=None,
            llm_cost=Decimal("0"),
            currency=self._cost_currency,
            basis="unknown",
        )


class VerifiedLiveVolcengineDoubaoASRProvider(VolcengineDoubaoASRProvider):
    """Source-reviewed, zero-retry live variant of the fail-closed adapter.

    The parent class can never make a network request because its contract is
    permanently non-production.  This separate class is the only reviewed
    live boundary.  It has no runtime ``production_ready`` switch, fixes the
    official endpoint/resource/model inherited from this module, and permits a
    signed media URL only through :class:`ReviewedASRMediaDelivery`.
    """

    def __init__(
        self,
        *,
        reviewed_media_delivery: ReviewedASRMediaDelivery,
        client: httpx.Client | None = None,
        **kwargs: Any,
    ) -> None:
        if not isinstance(reviewed_media_delivery, ReviewedASRMediaDelivery):
            raise TypeError("reviewed_media_delivery must be ReviewedASRMediaDelivery")
        if client is not None and client.follow_redirects:
            raise ValueError("Volcengine ASR live client must not follow redirects")
        super().__init__(client=client, **kwargs)
        # Selecting this distinct class is the reviewed source-level boundary;
        # no caller can flip the default provider through a constructor option.
        self.contract = replace(self.contract, production_ready=True)
        self._reviewed_media_delivery = reviewed_media_delivery

    def __repr__(self) -> str:
        return (
            "VerifiedLiveVolcengineDoubaoASRProvider("
            f"audio_format={self._audio_format!r}, language={self._language!r}, "
            "production_ready=True)"
        )

    def _submit_wire(self, request: ASRProviderRequest) -> _VolcengineASRWireRequest:
        return _submit_wire_request(
            request,
            audio_format=self._audio_format,
            language=self._language,
            reviewed_media_delivery=self._reviewed_media_delivery,
        )


def _provider_task_ref(task_key: str) -> str:
    if not task_key.strip():
        raise ValueError("ASR task key is required")
    return str(uuid5(NAMESPACE_URL, f"volcengine-doubao-asr:{task_key}"))


def _submit_wire_request(
    request: ASRProviderRequest,
    *,
    audio_format: str,
    language: str,
    reviewed_media_delivery: ReviewedASRMediaDelivery | None = None,
) -> _VolcengineASRWireRequest:
    """Build the inspectable non-secret submit shape; never sends it."""

    _validate_media_ref(request.media_ref, reviewed_media_delivery=reviewed_media_delivery)
    provider_task_ref = _provider_task_ref(request.task_key)
    return _VolcengineASRWireRequest(
        path=VOLCENGINE_ASR_SUBMIT_PATH,
        provider_task_ref=provider_task_ref,
        headers=(
            ("Content-Type", "application/json"),
            ("X-Api-Resource-Id", VOLCENGINE_ASR_ENGINE_VERSION),
            ("X-Api-Request-Id", provider_task_ref),
            ("X-Api-Sequence", "-1"),
        ),
        payload={
            "user": {"uid": "douyin-research-platform"},
            "audio": {
                "format": audio_format,
                "url": request.media_ref,
                "language": language,
            },
            "request": {
                "model_name": VOLCENGINE_ASR_MODEL_ID,
                "enable_itn": True,
                "enable_punc": True,
                "enable_ddc": False,
                "show_utterances": True,
                "enable_emotion_detection": False,
                "enable_gender_detection": False,
            },
        },
    )


def _poll_wire_request(provider_task_ref: str) -> _VolcengineASRWireRequest:
    """Build the inspectable non-secret poll shape; never sends it."""

    if not provider_task_ref.strip():
        raise ValueError("Volcengine ASR provider task reference is required")
    return _VolcengineASRWireRequest(
        path=VOLCENGINE_ASR_QUERY_PATH,
        provider_task_ref=provider_task_ref,
        headers=(
            ("Content-Type", "application/json"),
            ("X-Api-Resource-Id", VOLCENGINE_ASR_ENGINE_VERSION),
            ("X-Api-Request-Id", provider_task_ref),
        ),
        payload={},
    )


def _map_submit_state(
    status: str,
    provider_task_ref: str,
    *,
    unknown_cost: TaskCost,
) -> ASRProviderState:
    if status == _SUCCESS:
        return ASRProviderState(status="submitted", provider_task_ref=provider_task_ref)
    return ASRProviderState(
        status="failed",
        provider_task_ref=provider_task_ref,
        cost=unknown_cost,
        error_code=_provider_error_code(status),
    )


def _map_poll_state(
    status: str,
    provider_task_ref: str,
    *,
    payload: Mapping[str, Any] | None,
    evidence_builder: Callable[..., TranscriptEvidence],
    cost: TaskCost,
) -> ASRProviderState:
    """Map documented provider outcomes without a transport dependency."""

    if status in _RUNNING:
        return ASRProviderState(status="running", provider_task_ref=provider_task_ref)
    if status == _NO_SPEECH:
        return ASRProviderState(
            status="completed",
            provider_task_ref=provider_task_ref,
            evidence=evidence_builder({}, quality_status="no_speech"),
            cost=cost,
        )
    if status != _SUCCESS:
        return ASRProviderState(
            status="failed",
            provider_task_ref=provider_task_ref,
            cost=cost,
            error_code=_provider_error_code(status),
        )
    if payload is None:
        raise ValueError("Volcengine ASR completed response payload is required")
    return ASRProviderState(
        status="completed",
        provider_task_ref=provider_task_ref,
        evidence=evidence_builder(payload),
        cost=cost,
    )


def _parse_credential_free_https_url(media_ref: str):
    if not isinstance(media_ref, str):
        raise ValueError("Volcengine ASR media reference must be a credential-free HTTPS URL")
    parsed = urlsplit(media_ref)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("Volcengine ASR media reference must be a credential-free HTTPS URL")
    return parsed


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _validate_media_ref(
    media_ref: str,
    *,
    reviewed_media_delivery: ReviewedASRMediaDelivery | None = None,
) -> None:
    parsed = _parse_credential_free_https_url(media_ref)
    if parsed.query:
        if reviewed_media_delivery is None:
            raise ValueError(
                "Volcengine ASR media query requires ReviewedASRMediaDelivery"
            )
        if media_ref != reviewed_media_delivery.url:
            raise ValueError("Volcengine ASR media URL does not match reviewed delivery")
    elif (
        reviewed_media_delivery is not None
        and media_ref != reviewed_media_delivery.url
    ):
        raise ValueError("Volcengine ASR media URL does not match reviewed delivery")


def _status_code(response: httpx.Response) -> str:
    value = response.headers.get("X-Api-Status-Code", "")
    if not value.isdigit():
        raise RuntimeError("Volcengine ASR response status is missing or invalid")
    return value


def _provider_error_code(status: str) -> str:
    return f"volcengine_{status}" if status.isdigit() else "volcengine_unknown"


def _raise_sanitized_http_failure() -> None:
    """Erase upstream request/response details before surfacing a failure."""

    raise RuntimeError("Volcengine ASR HTTP request failed") from None


def _json_object(response: httpx.Response) -> Mapping[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        raise ValueError("Volcengine ASR response body is not valid JSON") from None
    if not isinstance(payload, Mapping):
        raise ValueError("Volcengine ASR response body must be an object")
    return payload


def _segment(value: object) -> TranscriptSegment:
    if not isinstance(value, Mapping):
        raise ValueError("Volcengine ASR utterance must be an object")
    text = value.get("text")
    start = value.get("start_time")
    end = value.get("end_time")
    if not isinstance(text, str) or type(start) is not int or type(end) is not int:
        raise ValueError("Volcengine ASR utterance fields are invalid")
    return TranscriptSegment(start_ms=start, end_ms=end, text=text)
