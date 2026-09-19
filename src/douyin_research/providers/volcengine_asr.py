"""Zero-retry adapter for Volcengine Doubao asynchronous file ASR."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any
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
_ALLOWED_FORMATS = frozenset({"raw", "wav", "mp3", "ogg"})


class VolcengineDoubaoASRProvider:
    """Map the official HTTP contract into provider-neutral ASR evidence.

    The adapter defaults to production_ready=False. Turning it on is an
    operator assertion that account-side billing and polling semantics were
    checked; the API key itself is never part of the execution contract.
    """

    provider_name = VOLCENGINE_ASR_PROVIDER
    max_retries = 0

    def __init__(
        self,
        *,
        api_key: str,
        audio_format: str,
        language: str = "zh-CN",
        cost_currency: str = "CNY",
        source_provider: str | None = None,
        timeout_seconds: int = 30,
        production_ready: bool = False,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Volcengine ASR API key is required")
        if audio_format not in _ALLOWED_FORMATS:
            raise ValueError("unsupported Volcengine ASR audio format")
        if not language.strip():
            raise ValueError("Volcengine ASR language is required")
        if not cost_currency.strip():
            raise ValueError("Volcengine ASR cost currency is required")
        self._api_key = api_key
        self._audio_format = audio_format
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
            production_ready=production_ready,
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
        _validate_media_ref(request.media_ref)
        provider_task_ref = _provider_task_ref(request.task_key)
        response = self._post(
            VOLCENGINE_ASR_SUBMIT_PATH,
            task_ref=provider_task_ref,
            include_sequence=True,
            payload={
                "user": {"uid": "douyin-research-platform"},
                "audio": {
                    "format": self._audio_format,
                    "url": request.media_ref,
                    "language": self._language,
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
        status = _status_code(response)
        if status == _SUCCESS:
            return ASRProviderState(
                status="submitted",
                provider_task_ref=provider_task_ref,
            )
        return ASRProviderState(
            status="failed",
            provider_task_ref=provider_task_ref,
            cost=self._unknown_cost(),
            error_code=_provider_error_code(status),
        )

    def poll(self, provider_task_ref: str) -> ASRProviderState:
        if not provider_task_ref.strip():
            raise ValueError("Volcengine ASR provider task reference is required")
        self._assert_contract_ready()
        response = self._post(
            VOLCENGINE_ASR_QUERY_PATH,
            task_ref=provider_task_ref,
            include_sequence=False,
            payload={},
        )
        status = _status_code(response)
        if status in _RUNNING:
            return ASRProviderState(
                status="running",
                provider_task_ref=provider_task_ref,
            )
        if status == _NO_SPEECH:
            return ASRProviderState(
                status="completed",
                provider_task_ref=provider_task_ref,
                evidence=self._evidence({}, quality_status="no_speech"),
                cost=self._unknown_cost(),
            )
        if status != _SUCCESS:
            return ASRProviderState(
                status="failed",
                provider_task_ref=provider_task_ref,
                cost=self._unknown_cost(),
                error_code=_provider_error_code(status),
            )

        payload = _json_object(response)
        return ASRProviderState(
            status="completed",
            provider_task_ref=provider_task_ref,
            evidence=self._evidence(payload),
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

    def _post(
        self,
        path: str,
        *,
        task_ref: str,
        include_sequence: bool,
        payload: Mapping[str, object],
    ) -> httpx.Response:
        headers = {
            "Content-Type": "application/json",
            "X-Api-Key": self._api_key,
            "X-Api-Resource-Id": VOLCENGINE_ASR_ENGINE_VERSION,
            "X-Api-Request-Id": task_ref,
        }
        if include_sequence:
            headers["X-Api-Sequence"] = "-1"
        try:
            response = self._client.post(
                VOLCENGINE_ASR_BASE_URL + path,
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            return response
        except httpx.HTTPError:
            raise RuntimeError("Volcengine ASR HTTP request failed") from None

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
            source_fingerprint="pending-request-source-fingerprint",
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


def _provider_task_ref(task_key: str) -> str:
    if not task_key.strip():
        raise ValueError("ASR task key is required")
    return str(uuid5(NAMESPACE_URL, f"volcengine-doubao-asr:{task_key}"))


def _validate_media_ref(media_ref: str) -> None:
    parsed = urlsplit(media_ref)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("Volcengine ASR media reference must be a credential-free HTTPS URL")


def _status_code(response: httpx.Response) -> str:
    value = response.headers.get("X-Api-Status-Code", "")
    if not value.isdigit():
        raise RuntimeError("Volcengine ASR response status is missing or invalid")
    return value


def _provider_error_code(status: str) -> str:
    return f"volcengine_{status}" if status.isdigit() else "volcengine_unknown"


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
