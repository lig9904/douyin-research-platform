"""Provider-neutral exception hierarchy and bounded failure diagnostics."""

from __future__ import annotations

import re
from collections.abc import Mapping
_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_ERROR_CODE = re.compile(r"[A-Za-z0-9._:-]{1,64}\Z")
_LOGICAL_CALL_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z",
    re.IGNORECASE,
)


def attach_provider_diagnostic(
    exc: Exception,
    *,
    http_status: int | None = None,
    provider_error_code: object = None,
    provider_request_id: object = None,
    logical_call_id: object = None,
) -> Exception:
    """Attach an allow-listed diagnostic without retaining provider payloads.

    Exception messages remain deliberately unsuitable for persistence: SDKs and
    HTTP libraries can include request URLs or bodies.  Callers must persist
    only :func:`provider_failure_summary`.
    """
    diagnostic: dict[str, object] = {}
    existing = getattr(exc, "provider_diagnostic", {})
    if isinstance(existing, Mapping):
        existing_status = existing.get("http_status")
        if (
            isinstance(existing_status, int)
            and not isinstance(existing_status, bool)
            and 100 <= existing_status <= 599
        ):
            diagnostic["http_status"] = existing_status
        for key, pattern in (
            ("provider_error_code", _ERROR_CODE),
            ("provider_request_id", _REQUEST_ID),
        ):
            token = _bounded_token(existing.get(key), pattern)
            if token is not None:
                diagnostic[key] = token
        existing_logical_id = existing.get("ledger_logical_call_id")
        if isinstance(existing_logical_id, str) and _LOGICAL_CALL_ID.fullmatch(existing_logical_id):
            diagnostic["ledger_logical_call_id"] = existing_logical_id.lower()
    if (
        isinstance(http_status, int)
        and not isinstance(http_status, bool)
        and 100 <= http_status <= 599
    ):
        diagnostic["http_status"] = http_status
    error_code = _bounded_token(provider_error_code, _ERROR_CODE)
    if error_code is not None:
        diagnostic["provider_error_code"] = error_code
    request_id = _bounded_token(provider_request_id, _REQUEST_ID)
    if request_id is not None:
        diagnostic["provider_request_id"] = request_id
    if isinstance(logical_call_id, str) and _LOGICAL_CALL_ID.fullmatch(logical_call_id):
        diagnostic["ledger_logical_call_id"] = logical_call_id.lower()
    exc.provider_diagnostic = diagnostic
    return exc


def provider_failure_summary(
    exc: BaseException,
    *,
    stage: str,
    item_count: int,
) -> dict[str, object]:
    """Return the only failure fields safe for a run summary or job result."""
    if stage not in {"discovery", "detail_enrichment", "finalize", "unknown"}:
        stage = "unknown"
    if isinstance(item_count, bool) or not isinstance(item_count, int) or item_count < 0:
        item_count = 0
    diagnostic = getattr(exc, "provider_diagnostic", {})
    if not isinstance(diagnostic, Mapping):
        diagnostic = {}
    safe: dict[str, object] = {
        "failure_schema": "provider_failure_v1",
        "status": "failed",
        "stage": stage,
        "item_count": item_count,
        "error_type": type(exc).__name__,
    }
    for key in (
        "http_status",
        "provider_error_code",
        "provider_request_id",
        "ledger_logical_call_id",
    ):
        if key in diagnostic:
            safe[key] = diagnostic[key]
    return safe


def _bounded_token(value: object, pattern: re.Pattern[str]) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    normalized = str(value)
    return normalized if pattern.fullmatch(normalized) else None


class ProviderError(RuntimeError):
    """Base provider failure."""


class ProviderAuthError(ProviderError):
    pass


class ProviderBalanceError(ProviderError):
    pass


class ProviderRateLimitError(ProviderError):
    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ProviderTemporaryError(ProviderError):
    pass


class ProviderPermanentError(ProviderError):
    pass


class ProviderNotFound(ProviderError):
    pass


class ProviderRestricted(ProviderError):
    pass


class ProviderSchemaError(ProviderError):
    pass


class ProviderBudgetError(ProviderError):
    pass
