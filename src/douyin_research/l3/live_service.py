"""Narrow live Ark entrypoint above the existing L3 execution state machine.

This module intentionally does not prepare evidence, approve privacy review,
transcribe media, promote videos, create budgets, or persist results itself.
It only turns an already-reviewed evidence *reference* plus explicit Ark
configuration into the existing one-shot ``L3ExecutionCoordinator`` call.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from uuid import UUID

import psycopg

from douyin_research.providers.volcengine_ark_l3 import (
    RECOMMENDED_ARK_MODEL_FAMILY,
    VOLCENGINE_ARK_L3_PROVIDER,
    VerifiedLiveVolcengineArkL3Provider,
)

from .evidence import L3EvidenceAssembler, L3EvidenceBundle
from .execution import L3_CONFIRMATION, L3ExecutionCoordinator, L3ExecutionRequest
from .results import L3_SCHEMA_VERSION


_FINGERPRINT_RE = re.compile(r"[0-9a-f]{64}\Z")
AUTOMATION_WORKER_IDENTITY_PATH = "f/content_research/automation_worker_identity"
LIVE_ARK_TASK_VERSION = "live-ark-l3-v1"
_REQUIRED_STRINGS = (
    "endpoint_id",
    "model_id",
    "model_revision",
    "expected_response_model",
    "pricing_version",
    "cost_currency",
)


@dataclass(frozen=True, slots=True)
class ReviewedL3Selection:
    """Reference to one current, persisted privacy-reviewed evidence snapshot."""

    video_id: UUID | str
    privacy_review_version: str
    expected_input_fingerprint: str
    expected_evidence_version: str
    prompt_version: str
    estimated_llm_cost: Decimal | str | int | float | None
    confirmation: str
    budget_date: date | None = None


@dataclass(frozen=True, slots=True, repr=False)
class LiveArkConfiguration:
    """In-memory-only Ark settings for one reviewed live execution."""

    api_key: str
    endpoint_id: str
    model_revision: str
    expected_response_model: str
    pricing_version: str
    input_cost_per_million_tokens: Decimal | str | int | float
    output_cost_per_million_tokens: Decimal | str | int | float
    model_id: str = RECOMMENDED_ARK_MODEL_FAMILY
    cost_currency: str = "CNY"
    timeout_seconds: int = 60


class _Assembler(Protocol):
    def assemble(
        self,
        video_id: UUID | str,
        *,
        privacy_reviewed: bool,
        privacy_review_version: str,
    ) -> L3EvidenceBundle: ...


class _Coordinator(Protocol):
    def run(
        self,
        request: L3ExecutionRequest,
        *,
        evidence_factory: Callable[[], L3EvidenceBundle],
        provider_factory: Callable[[], Any],
    ) -> dict[str, object]: ...


def execute_reviewed_live_ark(
    dsn: str,
    *,
    selection: ReviewedL3Selection,
    ark: LiveArkConfiguration,
    assembler_factory: Callable[[str], _Assembler] = L3EvidenceAssembler,
    coordinator_factory: Callable[[str], _Coordinator] = L3ExecutionCoordinator,
    provider_factory: Callable[..., Any] = VerifiedLiveVolcengineArkL3Provider,
    identity_reader: Callable[[str], str] | None = None,
) -> dict[str, object]:
    """Execute one approved Ark L3 task through the existing coordinator.

    An unavailable identity, stale evidence, or unapproved evidence reference
    returns a safe blocked result before a provider is constructed.
    Configuration errors raise without opening a provider connection. The
    coordinator retains ownership of task idempotency, budget reservation,
    failure accounting, and ``analysis_run``/``research_task_cost`` writes.
    """

    _validate_dsn(dsn)
    _validate_selection(selection)
    validate_live_ark_configuration(ark)
    if selection.confirmation != L3_CONFIRMATION:
        raise PermissionError("exact paid-operation confirmation is required")

    # Scheduled paid work has no authenticated human actor.  Its audit identity
    # is an operator-controlled Windmill variable, never a request field or
    # WM_END_USER_EMAIL.  A missing/malformed identity is a safe no-call block.
    try:
        worker_identity = _automation_actor(
            (identity_reader or _read_windmill_variable)(
                AUTOMATION_WORKER_IDENTITY_PATH
            )
        )
    except Exception:
        return _blocked("automation_identity_unavailable")

    try:
        assembled = assembler_factory(dsn).assemble(
            selection.video_id,
            privacy_reviewed=True,
            privacy_review_version=selection.privacy_review_version,
        )
    except (ValueError, psycopg.Error):
        return _blocked("evidence_unavailable_or_unapproved")

    if (
        assembled.input_fingerprint != selection.expected_input_fingerprint
        or assembled.evidence_version != selection.expected_evidence_version
    ):
        return _blocked("evidence_changed_since_review")

    execution = L3ExecutionRequest(
        video_id=assembled.video_id,
        task_key=live_ark_task_key(
            assembled.video_id,
            assembled.input_fingerprint,
            model_id=ark.model_id,
            model_revision=ark.model_revision,
            prompt_version=selection.prompt_version,
            schema_version=L3_SCHEMA_VERSION,
        ),
        provider=VOLCENGINE_ARK_L3_PROVIDER,
        model_id=ark.model_id,
        model_revision=ark.model_revision,
        prompt_version=selection.prompt_version,
        schema_version=L3_SCHEMA_VERSION,
        input_fingerprint=assembled.input_fingerprint,
        estimated_llm_cost=_optional_decimal(
            selection.estimated_llm_cost, "estimated LLM cost"
        ),
        cost_currency=ark.cost_currency,
        execute=True,
        confirmation=selection.confirmation,
        budget_date=selection.budget_date,
        actor=worker_identity,
        trigger_source="schedule",
    )

    # The lambda closes over a validated immutable snapshot.  The coordinator
    # verifies it again and re-checks the persisted review immediately before
    # budget reservation, so this service cannot substitute raw evidence.
    providers = []
    def construct_provider():
        provider = provider_factory(
            api_key=ark.api_key,
            endpoint_id=ark.endpoint_id,
            model_id=ark.model_id,
            model_revision=ark.model_revision,
            expected_response_model=ark.expected_response_model,
            cost_currency=ark.cost_currency,
            pricing_version=ark.pricing_version,
            input_cost_per_million_tokens=ark.input_cost_per_million_tokens,
            output_cost_per_million_tokens=ark.output_cost_per_million_tokens,
            timeout_seconds=ark.timeout_seconds,
        )
        providers.append(provider)
        return provider
    try:
        return coordinator_factory(dsn).run(execution,
            evidence_factory=lambda: assembled, provider_factory=construct_provider)
    finally:
        for provider in providers:
            close = getattr(provider, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    # A cleanup error must not turn a paid result into a retry.
                    pass


def _blocked(reason: str) -> dict[str, object]:
    return {
        "status": "blocked_missing_or_stale_l3_evidence",
        "execute": False,
        "external_calls": 0,
        "llm_calls": 0,
        "sdk_retries": 0,
        "reason": reason,
    }


def _validate_dsn(dsn: str) -> None:
    if not isinstance(dsn, str) or not dsn.strip():
        raise ValueError("L3 live service DSN is required")


def _validate_selection(selection: ReviewedL3Selection) -> None:
    for name in (
        "privacy_review_version",
        "expected_evidence_version",
        "prompt_version",
        "confirmation",
    ):
        value = getattr(selection, name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"L3 live selection {name} is required")
    if selection.budget_date is not None and not isinstance(
        selection.budget_date, date
    ):
        raise ValueError("L3 live selection budget date is invalid")
    if not isinstance(
        selection.expected_input_fingerprint, str
    ) or not _FINGERPRINT_RE.fullmatch(selection.expected_input_fingerprint):
        raise ValueError("L3 live selection input fingerprint is invalid")
    try:
        UUID(str(selection.video_id))
    except (TypeError, ValueError, AttributeError):
        raise ValueError("L3 live selection video ID is invalid") from None
    _optional_decimal(selection.estimated_llm_cost, "estimated LLM cost")


def validate_live_ark_configuration(ark: LiveArkConfiguration) -> None:
    if not isinstance(ark.api_key, str) or not ark.api_key.strip():
        raise ValueError("L3 live Ark API key is required")
    for name in _REQUIRED_STRINGS:
        value = getattr(ark, name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"L3 live Ark {name} is required")
    if "qwen" in ark.model_id.lower() and "flash" in ark.model_id.lower():
        raise ValueError("qwen-flash is not an allowed L3 live model")
    if (
        isinstance(ark.timeout_seconds, bool)
        or not isinstance(ark.timeout_seconds, int)
        or ark.timeout_seconds < 1
        or ark.timeout_seconds > 300
    ):
        raise ValueError("L3 live Ark timeout must be between 1 and 300 seconds")
    for value, label in ((ark.input_cost_per_million_tokens, "input token price"),
                         (ark.output_cost_per_million_tokens, "output token price")):
        if _decimal(value, label) <= 0:
            raise ValueError(f"L3 live {label} must be positive; zero placeholders are not prices")


def _decimal(value: Decimal | str | int | float, label: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"L3 live {label} must be a decimal") from None
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(f"L3 live {label} must be finite and non-negative")
    return parsed


def _optional_decimal(value: Decimal | str | int | float | None, label: str) -> Decimal | None:
    return None if value is None else _decimal(value, label)


def live_ark_task_key(
    video_id: UUID | str,
    evidence_fingerprint: str,
    *,
    model_id: str,
    model_revision: str,
    prompt_version: str,
    schema_version: str,
) -> str:
    """Return an idempotency key independent of caller-provided nonces.

    An automatic L3 run is uniquely about a video's reviewed evidence and the
    exact model/prompt/schema contract.  This excludes signed URLs, API keys,
    reviewers, and arbitrary request task keys, so retrying the same accepted
    work cannot create another paid run.
    """

    try:
        normalized_video_id = str(UUID(str(video_id)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("live Ark video ID is invalid") from exc
    if not isinstance(evidence_fingerprint, str) or not _FINGERPRINT_RE.fullmatch(
        evidence_fingerprint
    ):
        raise ValueError("live Ark evidence fingerprint is invalid")
    parts = (
        LIVE_ARK_TASK_VERSION,
        normalized_video_id,
        evidence_fingerprint,
        model_id,
        model_revision,
        prompt_version,
        schema_version,
    )
    if any(not isinstance(part, str) or not part.strip() for part in parts):
        raise ValueError("live Ark task identity is invalid")
    digest = hashlib.sha256(
        json.dumps(parts, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"live-ark-l3:{digest}"


def _read_windmill_variable(path: str) -> str:
    """Import Windmill only when a live scheduled execution needs it."""

    import wmill

    return wmill.get_variable(path)


def _automation_actor(identity: object) -> str:
    if not isinstance(identity, str):
        raise ValueError("automation worker identity is required")
    normalized = identity.strip()
    if not normalized or len(normalized) > 160:
        raise ValueError("automation worker identity is invalid")
    if any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in normalized):
        raise ValueError("automation worker identity contains control characters")
    return f"{normalized}/l3"
