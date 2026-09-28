from __future__ import annotations

import pytest

from douyin_research.l0l1.runner import DiscoverySource, L0L1Runner
from douyin_research.providers.errors import (
    ProviderPermanentError,
    attach_provider_diagnostic,
    provider_failure_summary,
)


class _FailingProvider:
    provider_name = "tikhub"
    platform_name = "douyin"
    video_batch_size = 50

    def search_videos(self, _query: str, **_kwargs):
        raise attach_provider_diagnostic(
            ProviderPermanentError("private body must never be persisted"),
            http_status=400,
            provider_error_code="INVALID_PARAMETER",
            provider_request_id="req-400-safe",
            logical_call_id="123e4567-e89b-42d3-a456-426614174000",
        )


class _Store:
    def __init__(self) -> None:
        self.finished: list[dict[str, object]] = []

    def create_run(self, *_args, **_kwargs):
        return "run-safe"

    def finish_run(self, _run_id, **kwargs):
        self.finished.append(kwargs)


class _Budget:
    def acquire(self, **_kwargs) -> None:
        return None

    def refund(self, **_kwargs) -> None:
        return None


def test_runner_persists_bounded_failure_summary_without_exception_message() -> None:
    store = _Store()
    runner = L0L1Runner(
        provider=_FailingProvider(), store=store, scorer=object(), budget=_Budget(),
    )

    with pytest.raises(ProviderPermanentError, match="private body"):
        runner.run([DiscoverySource("search", "keyword", "safe", {"query": "safe"})])

    summary = store.finished[-1]["summary"]
    assert summary == {
        "llm_calls": 0,
        "failure_schema": "provider_failure_v1",
        "status": "failed",
        "stage": "discovery",
        "item_count": 0,
        "error_type": "ProviderPermanentError",
        "http_status": 400,
        "provider_error_code": "INVALID_PARAMETER",
        "provider_request_id": "req-400-safe",
        "ledger_logical_call_id": "123e4567-e89b-42d3-a456-426614174000",
    }
    assert "private body" not in str(summary)


def test_failure_summary_revalidates_untrusted_exception_diagnostics() -> None:
    error = ProviderPermanentError("private response body")
    error.provider_diagnostic = {
        "http_status": True,
        "provider_error_code": "secret\nbody",
        "provider_request_id": "https://private.example/path?token=secret",
        "ledger_logical_call_id": "not-a-uuid",
        "raw_response": "private response body",
    }
    assert provider_failure_summary(error, stage="detail_enrichment", item_count=1) == {
        "failure_schema": "provider_failure_v1",
        "status": "failed",
        "stage": "detail_enrichment",
        "item_count": 1,
        "error_type": "ProviderPermanentError",
    }
