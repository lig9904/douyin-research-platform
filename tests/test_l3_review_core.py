from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest

from douyin_research.l3.review import (
    L3BudgetPreview,
    L3BudgetPreviewRequest,
    L3EvidenceManifest,
    _finite_nonnegative_decimal,
    _validate_budget_request,
    authorize_reviewer,
)


def test_authorize_reviewer_is_fail_closed_and_exact() -> None:
    assert authorize_reviewer(
        "reviewer@example.com", "other@example.com,reviewer@example.com"
    ) == "reviewer@example.com"
    assert authorize_reviewer(
        "reviewer@example.com", '["reviewer@example.com"]'
    ) == "reviewer@example.com"

    for email, allowlist in (
        (None, "reviewer@example.com"),
        ("", "reviewer@example.com"),
        ("Reviewer@example.com", "reviewer@example.com"),
        ("reviewer@example.com", ""),
        ("reviewer@example.com", "Reviewer@example.com"),
        ("not-allowed@example.com", "reviewer@example.com"),
    ):
        with pytest.raises(PermissionError):
            authorize_reviewer(email, allowlist)


def test_budget_preview_request_rejects_nonfinite_and_negative_costs() -> None:
    common = {
        "video_id": UUID(int=1),
        "privacy_review_version": "privacy-v1",
        "evidence_fingerprint": "a" * 64,
        "provider": "synthetic-l3",
        "cost_currency": "CNY",
    }
    assert _validate_budget_request(
        L3BudgetPreviewRequest(**common, estimated_llm_cost=0)
    ).estimated_llm_cost == Decimal("0")
    for value in (-1, float("nan"), float("inf"), "NaN"):
        with pytest.raises(ValueError):
            _validate_budget_request(
                L3BudgetPreviewRequest(**common, estimated_llm_cost=value)
            )
    assert _finite_nonnegative_decimal(None) is None


def test_manifest_and_budget_preview_never_include_raw_evidence() -> None:
    manifest = L3EvidenceManifest(
        UUID(int=1), "b" * 64, "l3-evidence-v1.0.0",
        ("metadata", "comments", "transcript"), "privacy-v1",
    )
    preview = L3BudgetPreview(
        "budget_capacity_preview_only", manifest, date(2026, 9, 20),
        "synthetic-l3", "CNY", Decimal("0"), Decimal("1"), 1,
        Decimal("0"), 0, Decimal("0"), 1,
    ).as_dict()

    assert preview["spent_cost"] == 0.0
    assert preview["estimated_llm_cost"] == 0.0
    assert preview["external_calls"] == 0
    assert preview["db_writes"] == 0
    assert preview["raw_evidence_included"] is False
    assert "evidence_bundle" not in preview
    assert "transcript" not in preview["manifest"]
