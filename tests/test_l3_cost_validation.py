from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import pytest

from douyin_research.l3 import (
    L3_SCHEMA_VERSION,
    L3ExecutionCoordinator,
    L3ExecutionRequest,
)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), Decimal("-Infinity")])
def test_l3_preview_rejects_non_finite_costs(value) -> None:
    request = L3ExecutionRequest(
        video_id=UUID(int=0),
        task_key="non-finite-preview",
        provider="synthetic",
        model_id="synthetic-model",
        model_revision="revision-1",
        prompt_version="prompt-v1",
        schema_version=L3_SCHEMA_VERSION,
        input_fingerprint="synthetic-fingerprint",
        estimated_llm_cost=value,
        cost_currency="CNY",
    )

    with pytest.raises(ValueError, match="cost must be finite"):
        L3ExecutionCoordinator("unused-preview-dsn").run(
            request,
            evidence_factory=lambda: pytest.fail("evidence must not be loaded"),
            provider_factory=lambda: pytest.fail("provider must not be loaded"),
        )
