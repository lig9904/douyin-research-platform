"""Safe input binding for the reviewed live Volcengine ASR boundary.

This is intentionally a small composition layer: the execution state machine
remains :class:`ASRExecutionCoordinator`, and network behavior remains in the
reviewed provider adapter.  It never persists or returns a media URL or API
key.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable
from urllib.parse import urlsplit
from uuid import UUID

from douyin_research.l2.asr_execution import (
    ASR_CONFIRMATION,
    ASRExecutionCoordinator,
    ASRExecutionRequest,
    ASRProvider,
)
from douyin_research.providers.volcengine_asr import (
    VOLCENGINE_ASR_ENGINE_VERSION,
    VOLCENGINE_ASR_MODEL_ID,
    VOLCENGINE_ASR_MODEL_REVISION,
    VOLCENGINE_ASR_PROVIDER,
    ReviewedASRMediaDelivery,
    VerifiedLiveVolcengineDoubaoASRProvider,
)


LIVE_ASR_TASK_VERSION = "live-volcengine-asr-v1"


@dataclass(frozen=True, slots=True, repr=False)
class LiveASRRequest:
    dsn: str
    video_id: UUID | str
    media_url: str
    media_review_version: str
    media_query_sha256: str | None
    audio_format: str
    source_fingerprint: str
    api_key: str
    reviewed_asset_id: UUID | str
    source_provider: str | None = None
    language: str = "zh-CN"
    max_polls: int = 1
    # TikHub-like quote fields must stay None until a real, versioned supplier
    # price has been captured.  This facade never substitutes zero.
    estimated_api_cost: Decimal | float | int | None = None
    estimated_asr_cost: Decimal | float | int | None = None
    cost_currency: str = "CNY"


def live_asr_task_key(
    video_id: UUID | str,
    source_fingerprint: str,
    *,
    model_id: str = VOLCENGINE_ASR_MODEL_ID,
    model_revision: str = VOLCENGINE_ASR_MODEL_REVISION,
    engine_version: str = VOLCENGINE_ASR_ENGINE_VERSION,
) -> str:
    """Create a stable key from identity and content/model versions only.

    The delivery URL can rotate while referring to the same reviewed media, so
    it is purposefully absent.  Query strings and credentials never enter this
    task key.
    """

    try:
        normalized_video_id = str(UUID(str(video_id)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("video_id must be a UUID") from exc
    if not isinstance(source_fingerprint, str) or not source_fingerprint.strip():
        raise ValueError("source_fingerprint is required")
    parts = (
        LIVE_ASR_TASK_VERSION,
        normalized_video_id,
        source_fingerprint.strip(),
        model_id,
        model_revision,
        engine_version,
    )
    if any(not isinstance(part, str) or not part.strip() for part in parts):
        raise ValueError("live ASR task identity is invalid")
    digest = hashlib.sha256(
        json.dumps(parts, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"live-asr:{digest}"


def _validate_live_request(request: LiveASRRequest) -> None:
    required = {
        "dsn": request.dsn,
        "media_url": request.media_url,
        "media_review_version": request.media_review_version,
        "source_fingerprint": request.source_fingerprint,
        "api_key": request.api_key,
        "audio_format": request.audio_format,
        "cost_currency": request.cost_currency,
    }
    missing = [
        name
        for name, value in required.items()
        if not isinstance(value, str) or not value.strip()
    ]
    if missing:
        raise ValueError(f"required live ASR fields are missing: {missing}")
    if request.audio_format != "wav":
        raise ValueError("reviewed normalized audio requires wav format")
    if (
        not isinstance(request.max_polls, int)
        or isinstance(request.max_polls, bool)
        or request.max_polls < 0
    ):
        raise ValueError("max_polls must be a non-negative integer")
    parsed = urlsplit(request.media_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.fragment:
        raise ValueError(
            "media_url must be a credential-free HTTPS URL or reviewed signed HTTPS URL"
        )


def reviewed_media_delivery(request: LiveASRRequest) -> ReviewedASRMediaDelivery:
    """Construct the provider's review record before its HTTP client exists."""

    _validate_live_request(request)
    return ReviewedASRMediaDelivery(
        url=request.media_url,
        review_version=request.media_review_version,
        query_sha256=request.media_query_sha256,
    )


def _worker_identity() -> str:
    try:
        import wmill
        value = wmill.get_variable("f/content_research/automation_worker_identity")
        if not isinstance(value, str) or not value.strip() or len(value) > 128 or any(ord(c) < 32 for c in value):
            raise ValueError
        return value.strip() + "/asr"
    except Exception:
        raise RuntimeError("ASR worker identity unavailable") from None


def execution_request(request: LiveASRRequest) -> ASRExecutionRequest:
    """Build the coordinator request without silently inventing a price."""

    _validate_live_request(request)
    return ASRExecutionRequest(
        video_id=request.video_id,
        task_key=live_asr_task_key(request.video_id, request.source_fingerprint),
        provider=VOLCENGINE_ASR_PROVIDER,
        model_id=VOLCENGINE_ASR_MODEL_ID,
        model_revision=VOLCENGINE_ASR_MODEL_REVISION,
        engine_version=VOLCENGINE_ASR_ENGINE_VERSION,
        source_fingerprint=request.source_fingerprint,
        media_ref=request.media_url,
        estimated_api_cost=request.estimated_api_cost,
        estimated_asr_cost=request.estimated_asr_cost,
        cost_currency=request.cost_currency,
        execute=True,
        confirmation=ASR_CONFIRMATION,
        max_polls=request.max_polls,
        actor=_worker_identity(),
        trigger_source="schedule",
        reviewed_asset_id=request.reviewed_asset_id,
        media_review_version=request.media_review_version,
    )


class LiveASRService:
    """Run the reviewed live adapter through the existing idempotent coordinator."""

    def __init__(self, dsn: str) -> None:
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("dsn is required")
        self._coordinator = ASRExecutionCoordinator(dsn)

    def run(
        self,
        request: LiveASRRequest,
        *,
        provider_factory: Callable[[LiveASRRequest, ReviewedASRMediaDelivery], ASRProvider]
        | None = None,
    ) -> dict[str, object]:
        if request.dsn != self._coordinator.dsn:
            raise ValueError("request dsn does not match live ASR service")
        delivery = reviewed_media_delivery(request)
        core_request = execution_request(request)
        factory = provider_factory or _verified_provider_factory
        # The coordinator is responsible for no-resubmit behavior for jobs in
        # submitted/running states. It returns a redacted result only.
        result = self._coordinator.run(
            core_request,
            provider_factory=lambda: factory(request, delivery),
        )
        return {
            **result,
            "task_key": core_request.task_key,
            "provider": VOLCENGINE_ASR_PROVIDER,
            "model_id": VOLCENGINE_ASR_MODEL_ID,
            "model_revision": VOLCENGINE_ASR_MODEL_REVISION,
            "engine_version": VOLCENGINE_ASR_ENGINE_VERSION,
            "cost_basis": "unknown" if (
                request.estimated_api_cost is None or request.estimated_asr_cost is None
            ) else "estimated",
            "actor_recorded": True,
            "media_url_recorded": False,
        }


def _verified_provider_factory(
    request: LiveASRRequest,
    delivery: ReviewedASRMediaDelivery,
) -> VerifiedLiveVolcengineDoubaoASRProvider:
    return VerifiedLiveVolcengineDoubaoASRProvider(
        reviewed_media_delivery=delivery,
        api_key=request.api_key,
        audio_format=request.audio_format,
        source_fingerprint=request.source_fingerprint,
        language=request.language,
        cost_currency=request.cost_currency,
        source_provider=request.source_provider,
        timeout_seconds=30,
    )
