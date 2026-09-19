"""Provider-neutral exception hierarchy."""

from __future__ import annotations


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
