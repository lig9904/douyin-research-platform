"""Auditable TikHub REST transport; explicit opt-in legacy SDK compatibility."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from .endpoints import EndpointSpec
from .errors import (
    ProviderAuthError,
    ProviderBalanceError,
    ProviderNotFound,
    ProviderPermanentError,
    ProviderRateLimitError,
    ProviderTemporaryError,
    attach_provider_diagnostic,
)


@dataclass(slots=True)
class TransportResult:
    payload: dict[str, Any]
    http_status: int | None
    provider_request_id: str | None
    retry_count: int = 0
    mode: str = "sdk"
    # None means opaque transport (including SDK-internal retries), not zero attempts.
    attempts: tuple[dict[str, Any], ...] | None = None


class ProviderTransport(Protocol):
    def call(self, spec: EndpointSpec, kwargs: dict[str, Any]) -> TransportResult: ...


class TikHubTransport:
    """REST exposes every attempt. Legacy SDK attempts remain explicitly unknown."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.tikhub.io",
        timeout: float = 30.0,
        max_retries: int = 3,
        http_client: httpx.Client | None = None,
        prefer_sdk: bool = False,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 0:
            raise ValueError("max_retries must be a non-negative integer")
        self.prefer_sdk = prefer_sdk
        self._http_client = http_client
        self._sdk_client: Any = None

    def _get_sdk_client(self) -> Any:
        if self._sdk_client is not None:
            return self._sdk_client
        from tikhub import TikHub

        self._sdk_client = TikHub(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
            max_retries=self.max_retries,
        )
        return self._sdk_client

    def close(self) -> None:
        if self._sdk_client is not None:
            self._sdk_client.close()
            self._sdk_client = None
        if self._http_client is not None:
            self._http_client.close()

    def call(self, spec: EndpointSpec, kwargs: dict[str, Any]) -> TransportResult:
        if self.prefer_sdk and spec.sdk_resource and spec.sdk_method:
            client = self._get_sdk_client()
            resource = getattr(client, spec.sdk_resource, None)
            method = getattr(resource, spec.sdk_method, None) if resource is not None else None
            if method is not None:
                try:
                    payload = method(**kwargs)
                    return TransportResult(
                        payload=payload,
                        http_status=200,
                        provider_request_id=_request_id(payload),
                        mode="sdk",
                    )
                except Exception as exc:  # SDK types mapped below without leaking dependency upward
                    mapped = _map_sdk_exception(exc)
                    if mapped is not None:
                        raise mapped from exc
                    raise

        return self._rest_call(spec, kwargs)

    def _rest_call(self, spec: EndpointSpec, kwargs: dict[str, Any]) -> TransportResult:
        owned_client = self._http_client is None
        client = self._http_client or httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout,
            headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"},
        )
        retry_count = 0
        attempts: list[dict[str, Any]] = []
        try:
            while True:
                try:
                    request_kwargs: dict[str, Any] = {}
                    if spec.request_style == "query":
                        request_kwargs["params"] = kwargs
                    elif spec.request_style == "json":
                        request_kwargs["json"] = kwargs.get("body", kwargs)
                    attempts.append({"http_status": None})
                    response = client.request(spec.http_method, spec.path, **request_kwargs)
                    attempts[-1]["http_status"] = response.status_code
                except httpx.RequestError as exc:
                    attempts[-1]["error_type"] = type(exc).__name__
                    if retry_count >= self.max_retries - 1:
                        raise attach_provider_diagnostic(
                            ProviderTemporaryError("TikHub REST request failed"),
                        ) from exc
                    retry_count += 1
                    time.sleep(_backoff(retry_count))
                    continue

                if response.status_code == 429:
                    if retry_count >= self.max_retries - 1:
                        retry_after = _retry_after(response)
                        raise _response_failure(
                            ProviderRateLimitError(
                                "TikHub rate limited REST fallback", retry_after=retry_after
                            ),
                            response,
                        )
                    retry_count += 1
                    time.sleep(_retry_after(response) or _backoff(retry_count))
                    continue

                if 500 <= response.status_code < 600:
                    if retry_count >= self.max_retries - 1:
                        raise _response_failure(
                            ProviderTemporaryError(f"TikHub REST {response.status_code}"),
                            response,
                        )
                    retry_count += 1
                    time.sleep(_backoff(retry_count))
                    continue

                if response.status_code == 401:
                    raise _response_failure(ProviderAuthError("TikHub authentication failed"), response)
                if response.status_code == 402:
                    raise _response_failure(
                        ProviderBalanceError("TikHub balance/credit error"), response,
                    )
                if response.status_code == 403:
                    text = response.text.lower()
                    if any(x in text for x in ("balance", "credit", "余额")):
                        raise _response_failure(
                            ProviderBalanceError("TikHub balance/credit error"), response,
                        )
                    raise _response_failure(
                        ProviderPermanentError("TikHub permission/quota error"), response,
                    )
                if response.status_code == 404:
                    raise _response_failure(
                        ProviderNotFound("TikHub endpoint/resource not found"), response,
                    )
                if response.status_code >= 400:
                    raise _response_failure(
                        ProviderPermanentError(f"TikHub REST {response.status_code}"), response,
                    )

                payload = response.json()
                return TransportResult(
                    payload=payload,
                    http_status=response.status_code,
                    provider_request_id=response.headers.get("x-request-id") or _request_id(payload),
                    retry_count=retry_count,
                    mode="rest",
                    attempts=tuple(attempts),
                )
        except Exception as exc:
            # Include the final failed attempt. Never include URLs, keys or response bodies.
            exc.provider_attempts = tuple(attempts)
            raise
        finally:
            if owned_client:
                client.close()


def _map_sdk_exception(exc: Exception) -> Exception | None:
    """Map SDK errors lazily so provider code does not expose TikHub exception types."""
    try:
        from tikhub import (
            TikHubAuthError,
            TikHubConnectionError,
            TikHubHTTPError,
            TikHubNotFoundError,
            TikHubPermissionError,
            TikHubRateLimitError,
            TikHubServerError,
            TikHubUpstreamError,
        )
    except Exception:
        return None

    if isinstance(exc, TikHubAuthError):
        return attach_provider_diagnostic(
            ProviderAuthError("TikHub authentication failed"),
            provider_request_id=getattr(exc, "request_id", None),
        )
    if isinstance(exc, TikHubRateLimitError):
        return attach_provider_diagnostic(
            ProviderRateLimitError("TikHub rate limited", retry_after=getattr(exc, "retry_after", None)),
            http_status=getattr(exc, "status_code", None),
            provider_request_id=getattr(exc, "request_id", None),
        )
    if isinstance(exc, TikHubNotFoundError):
        return attach_provider_diagnostic(
            ProviderNotFound("TikHub endpoint/resource not found"),
            http_status=getattr(exc, "status_code", None),
            provider_request_id=getattr(exc, "request_id", None),
        )
    if isinstance(exc, TikHubPermissionError):
        body = getattr(exc, "response_body", None)
        text = str(body).lower()
        if any(x in text for x in ("balance", "credit", "余额")):
            return attach_provider_diagnostic(
                ProviderBalanceError("TikHub balance/credit error"),
                http_status=getattr(exc, "status_code", None),
                provider_request_id=getattr(exc, "request_id", None),
            )
        return attach_provider_diagnostic(
            ProviderPermanentError("TikHub permission/quota error"),
            http_status=getattr(exc, "status_code", None),
            provider_request_id=getattr(exc, "request_id", None),
        )
    if isinstance(exc, (TikHubServerError, TikHubUpstreamError, TikHubConnectionError)):
        return attach_provider_diagnostic(
            ProviderTemporaryError("TikHub temporary upstream failure"),
            http_status=getattr(exc, "status_code", None),
            provider_request_id=getattr(exc, "request_id", None),
        )
    if isinstance(exc, TikHubHTTPError):
        status = getattr(exc, "status_code", None)
        body = getattr(exc, "response_body", None)
        text = f"{body} {exc}".lower()
        if status == 402 or any(x in text for x in ("balance", "credit", "余额")):
            return attach_provider_diagnostic(
                ProviderBalanceError("TikHub balance/credit error"),
                http_status=status,
                provider_request_id=getattr(exc, "request_id", None),
            )
        if status == 401:
            return attach_provider_diagnostic(
                ProviderAuthError("TikHub authentication failed"),
                http_status=status,
                provider_request_id=getattr(exc, "request_id", None),
            )
        if status == 404:
            return attach_provider_diagnostic(
                ProviderNotFound("TikHub endpoint/resource not found"),
                http_status=status,
                provider_request_id=getattr(exc, "request_id", None),
            )
        if status == 429:
            return attach_provider_diagnostic(
                ProviderRateLimitError("TikHub rate limited", retry_after=getattr(exc, "retry_after", None)),
                http_status=status,
                provider_request_id=getattr(exc, "request_id", None),
            )
        if status is not None and 500 <= status < 600:
            return attach_provider_diagnostic(
                ProviderTemporaryError("TikHub temporary upstream failure"),
                http_status=status,
                provider_request_id=getattr(exc, "request_id", None),
            )
        return attach_provider_diagnostic(
            ProviderPermanentError("TikHub permanent request failure"),
            http_status=status,
            provider_request_id=getattr(exc, "request_id", None),
        )
    return None


def _request_id(payload: Any) -> str | None:
    return payload.get("request_id") if isinstance(payload, dict) else None


def _response_failure(exc: Exception, response: httpx.Response) -> Exception:
    """Extract only bounded diagnostic tokens from an HTTP error response."""
    return attach_provider_diagnostic(
        exc,
        http_status=response.status_code,
        provider_error_code=_response_error_code(response),
        provider_request_id=(
            response.headers.get("x-request-id")
            or response.headers.get("request-id")
        ),
    )


def _response_error_code(response: httpx.Response) -> object:
    try:
        payload = response.json()
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    for key in ("code", "status_code", "error_code"):
        value = payload.get(key)
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            return value
    return None


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def _backoff(retry_count: int) -> float:
    return min(30.0, 0.5 * (2 ** max(0, retry_count - 1)))
