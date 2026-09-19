from __future__ import annotations

import importlib.util
import inspect
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
BACKEND = ROOT / "windmill/f/content_research/research_dashboard.raw_app/backend"


def _load(file_stem: str):
    path = BACKEND / f"{file_stem}.py"
    name = f"{file_stem}_backend"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


prepare = _load("prepare_l3_review")
approve = _load("approve_l3_review")
budget = _load("preview_l3_budget")

DB = {
    "host": "127.0.0.1",
    "port": 5432,
    "user": "review-test",
    "password": "DB_SECRET_SENTINEL",
    "dbname": "review-test",
    "sslmode": "disable",
}
ALLOWLIST = "reviewer@example.com"
FINGERPRINT = "a" * 64


class _Result:
    def __init__(self, value):
        self.value = value

    def as_dict(self):
        return self.value


def test_review_backends_expose_no_actor_or_execution_parameters() -> None:
    forbidden = {"actor", "execute", "confirmation", "task_key", "provider", "model_id"}
    for module in (prepare, approve, budget):
        parameters = set(inspect.signature(module.main).parameters)
        assert forbidden.isdisjoint(parameters)


@pytest.mark.parametrize("module", [prepare, approve, budget])
def test_review_backends_fail_closed_without_end_user_identity(module, monkeypatch) -> None:
    monkeypatch.delenv("WM_END_USER_EMAIL", raising=False)
    kwargs = {
        "db": DB,
        "reviewer_allowlist": ALLOWLIST,
        "video_id": "00000000-0000-0000-0000-000000000001",
    }
    if module is prepare:
        kwargs["privacy_review_version"] = "privacy-v1"
    elif module is approve:
        kwargs.update(
            privacy_review_version="privacy-v1",
            evidence_fingerprint=FINGERPRINT,
            evidence_version="l3-evidence-v1.0.0",
            evidence_modalities=["metadata", "comments", "transcript"],
            idempotency_key="00000000-0000-4000-8000-000000000001",
        )
    else:
        kwargs.update(
            budget_preview_config=json.dumps(
                {
                    "provider": "synthetic",
                    "model_id": "model",
                    "model_revision": "revision-1",
                    "prompt_version": "prompt-v1",
                    "pricing_version": "pricing-v1",
                    "estimated_llm_cost": 0,
                    "cost_currency": "CNY",
                }
            ),
            privacy_review_version="privacy-v1",
            evidence_fingerprint=FINGERPRINT,
        )
    with pytest.raises(PermissionError, match="end-user email"):
        module.main(**kwargs)


def test_prepare_and_approve_outputs_are_explicit_redacted_whitelists(monkeypatch) -> None:
    monkeypatch.setenv("WM_END_USER_EMAIL", "reviewer@example.com")
    raw_sentinel = "RAW_TRANSCRIPT_SENTINEL"

    class FakeService:
        def __init__(self, dsn):
            assert "DB_SECRET_SENTINEL" in dsn

        def prepare(self, *args, **kwargs):
            return _Result(
                {
                    "video_id": "internal-video-id",
                    "evidence_fingerprint": FINGERPRINT,
                    "evidence_version": "l3-evidence-v1.0.0",
                    "evidence_modalities": ["metadata", "comments", "transcript"],
                    "privacy_review_version": "privacy-v1",
                    "evidence_bundle": raw_sentinel,
                }
            )

        def approve(self, request):
            assert request.actor == "reviewer@example.com"
            return _Result(
                {
                    "approved_at": "2026-09-20T00:00:00+00:00",
                    "idempotent_replay": False,
                    "evidence_fingerprint": FINGERPRINT,
                    "evidence_version": "l3-evidence-v1.0.0",
                    "evidence_modalities": ["metadata", "comments", "transcript"],
                    "privacy_review_version": "privacy-v1",
                    "actor": "reviewer@example.com",
                    "approval_id": "internal-approval-id",
                    "evidence_bundle": raw_sentinel,
                }
            )

    monkeypatch.setattr(prepare, "L3ReviewService", FakeService)
    monkeypatch.setattr(approve, "L3ReviewService", FakeService)
    prepared = prepare.main(DB, ALLOWLIST, "video-id", "privacy-v1")
    approved = approve.main(
        DB,
        ALLOWLIST,
        "00000000-0000-0000-0000-000000000001",
        "privacy-v1",
        FINGERPRINT,
        "l3-evidence-v1.0.0",
        ["metadata", "comments", "transcript"],
        "00000000-0000-4000-8000-000000000001",
    )
    serialized = json.dumps([prepared, approved])
    assert raw_sentinel not in serialized
    assert "DB_SECRET_SENTINEL" not in serialized
    assert "reviewer@example.com" not in serialized
    assert "internal-approval-id" not in serialized
    assert prepared["db_writes"] == 0
    assert approved["db_writes"] == 1
    assert prepared["external_calls"] == approved["external_calls"] == 0
    assert prepared["paid_execution_available"] is False
    assert approved["paid_execution_available"] is False


@pytest.mark.parametrize("estimate", ["NaN", "Infinity", -1, 0.0000001])
def test_budget_static_config_rejects_unsafe_estimates(estimate) -> None:
    config = {
        "provider": "synthetic",
        "model_id": "model",
        "model_revision": "revision-1",
        "prompt_version": "prompt-v1",
        "pricing_version": "pricing-v1",
        "estimated_llm_cost": estimate,
        "cost_currency": "CNY",
    }
    with pytest.raises(ValueError, match="configuration is invalid"):
        budget._preview_config(json.dumps(config))


def test_budget_wrapper_is_read_only_and_preserves_zero(monkeypatch) -> None:
    monkeypatch.setenv("WM_END_USER_EMAIL", "reviewer@example.com")

    class FakeService:
        def __init__(self, dsn):
            assert "DB_SECRET_SENTINEL" in dsn

        def preview_budget(self, request):
            assert request.estimated_llm_cost == 0
            return _Result(
                {
                    "status": "budget_capacity_preview_only",
                    "budget_date": "2026-09-20",
                    "max_cost": 0.0,
                    "max_requests": 0,
                    "spent_cost": 0.0,
                    "used_requests": 0,
                    "next_cost": 0.0,
                    "next_requests": 1,
                    "estimated_llm_cost": 0.0,
                    "cost_currency": "CNY",
                }
            )

    monkeypatch.setattr(budget, "L3ReviewService", FakeService)
    result = budget.main(
        DB,
        ALLOWLIST,
        json.dumps(
            {
                "provider": "synthetic",
                "model_id": "model",
                "model_revision": "revision-1",
                "prompt_version": "prompt-v1",
                "pricing_version": "pricing-v1",
                "estimated_llm_cost": 0,
                "cost_currency": "CNY",
            }
        ),
        "00000000-0000-0000-0000-000000000001",
        "privacy-v1",
        FINGERPRINT,
    )
    assert result["estimated_llm_cost"] == 0.0
    assert result["budget"]["max_cost"] == 0.0
    assert result["budget"]["configured"] is True
    assert result["maximum_external_calls"] == 0
    assert result["maximum_llm_calls"] == 0
    assert result["db_writes"] == 0
    assert result["paid_execution_available"] is False


@pytest.mark.parametrize(
    ("status", "configured"),
    [
        ("approval_missing", None),
        ("approval_stale", None),
        ("candidate_stale", None),
        ("future_unknown_status", None),
        ("budget_missing", False),
        ("budget_capacity_preview_only", True),
    ],
)
def test_budget_configured_is_only_claimed_after_budget_read(status, configured) -> None:
    assert budget._budget_configured(status) is configured


def test_backend_failures_are_replaced_with_fixed_codes(monkeypatch) -> None:
    monkeypatch.setenv("WM_END_USER_EMAIL", "reviewer@example.com")

    class FailingService:
        def __init__(self, dsn):
            pass

        def prepare(self, *args, **kwargs):
            raise RuntimeError("SENSITIVE_DB_HOST_AND_SQL_SENTINEL")

        def approve(self, *args, **kwargs):
            raise RuntimeError("SENSITIVE_DB_HOST_AND_SQL_SENTINEL")

        def preview_budget(self, *args, **kwargs):
            raise RuntimeError("SENSITIVE_DB_HOST_AND_SQL_SENTINEL")

    monkeypatch.setattr(prepare, "L3ReviewService", FailingService)
    monkeypatch.setattr(approve, "L3ReviewService", FailingService)
    monkeypatch.setattr(budget, "L3ReviewService", FailingService)
    with pytest.raises(RuntimeError) as prepared:
        prepare.main(DB, ALLOWLIST, "video-id", "privacy-v1")
    with pytest.raises(RuntimeError) as approved:
        approve.main(
            DB,
            ALLOWLIST,
            "00000000-0000-0000-0000-000000000001",
            "privacy-v1",
            FINGERPRINT,
            "l3-evidence-v1.0.0",
            ["metadata", "comments", "transcript"],
            "00000000-0000-4000-8000-000000000001",
        )
    with pytest.raises(RuntimeError) as previewed:
        budget.main(
            DB,
            ALLOWLIST,
            json.dumps(
                {
                    "provider": "synthetic",
                    "model_id": "model",
                    "model_revision": "revision-1",
                    "prompt_version": "prompt-v1",
                    "pricing_version": "pricing-v1",
                    "estimated_llm_cost": 0,
                    "cost_currency": "CNY",
                }
            ),
            "00000000-0000-0000-0000-000000000001",
            "privacy-v1",
            FINGERPRINT,
        )
    messages = [str(prepared.value), str(approved.value), str(previewed.value)]
    assert messages == [
        "L3_REVIEW_CANDIDATE_UNAVAILABLE",
        "L3_REVIEW_APPROVAL_FAILED",
        "L3_BUDGET_PREVIEW_FAILED",
    ]
    assert all("SENSITIVE" not in message for message in messages)


def test_review_backend_resources_are_static_and_no_secret_is_in_source() -> None:
    for name in ("prepare_l3_review", "approve_l3_review", "preview_l3_budget"):
        metadata = (BACKEND / f"{name}.yaml").read_text()
        source = (BACKEND / f"{name}.py").read_text()
        assert "type: static" in metadata
        assert "$res:f/content_research/research_db" in metadata
        assert "$var:f/content_research/l3_privacy_reviewers" in metadata
        assert "WM_EMAIL" not in source
        assert "WM_USERNAME" not in source
        assert (
            "#douyin-research-platform@git+https://github.com/lig9904/"
            "douyin-research-platform@1558677c40cc22239e660e269738619dfd05388d"
        ) in source
        assert "#psycopg[binary]==3.3.6" in source
        lock = (BACKEND / f"{name}.lock").read_text()
        assert "douyin-research-platform @ git+https://github.com/lig9904/" in lock
        assert "psycopg==3.3.6" in lock
        assert "psycopg-binary==3.3.6" in lock
        assert "psycopg[binary]" not in lock
        assert "provider_factory" not in source
        assert "L3ExecutionCoordinator" not in source
        assert "execute:" not in metadata
        assert "task_key:" not in metadata
    budget_metadata = (BACKEND / "preview_l3_budget.yaml").read_text()
    assert "$var:f/content_research/l3_budget_preview_config" in budget_metadata

    frontend = (
        ROOT
        / "windmill/f/content_research/research_dashboard.raw_app/src/components/L3ReviewPanel.tsx"
    ).read_text()
    assert "setError(e instanceof Error ? e.message" not in frontend
    assert "SENSITIVE" not in frontend
    assert '<span>审核对象 ID</span><code>{videoId}</code>' in frontend
