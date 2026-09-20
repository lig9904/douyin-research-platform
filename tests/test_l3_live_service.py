from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest

from douyin_research.l3.evidence import L3_EVIDENCE_VERSION, L3EvidenceBundle
from douyin_research.l3.execution import (
    L3_CONFIRMATION,
    L3ExecutionCoordinator,
    L3ExecutionRequest,
)
from douyin_research.l3.live_service import (
    AUTOMATION_WORKER_IDENTITY_PATH,
    LiveArkConfiguration,
    ReviewedL3Selection,
    execute_reviewed_live_ark,
    live_ark_task_key,
)
from douyin_research.l3.results import L3_SCHEMA_VERSION
from douyin_research.providers.volcengine_ark_l3 import VOLCENGINE_ARK_L3_PROVIDER


def bundle() -> L3EvidenceBundle:
    return L3EvidenceBundle.issue(
        video_id=uuid4(),
        evidence_bundle={
            "evidence_version": L3_EVIDENCE_VERSION,
            "modalities": ["metadata"],
            "privacy_review": {"reviewed": True, "version": "privacy-v1"},
        },
    )


def selection(evidence: L3EvidenceBundle, **overrides: object) -> ReviewedL3Selection:
    values: dict[str, object] = {
        "video_id": evidence.video_id,
        "privacy_review_version": "privacy-v1",
        "expected_input_fingerprint": evidence.input_fingerprint,
        "expected_evidence_version": evidence.evidence_version,
        "prompt_version": "l3-prompt-v1",
        "estimated_llm_cost": Decimal("0.24"),
        "confirmation": L3_CONFIRMATION,
    }
    values.update(overrides)
    return ReviewedL3Selection(**values)


def ark(**overrides: object) -> LiveArkConfiguration:
    values: dict[str, object] = {
        "api_key": "ark-secret-not-for-output",
        "endpoint_id": "ep-opaque",
        "model_revision": "seed-revision-1",
        "expected_response_model": "ep-opaque",
        "pricing_version": "ark-price-v1",
        "input_cost_per_million_tokens": Decimal("1"),
        "output_cost_per_million_tokens": Decimal("2"),
    }
    values.update(overrides)
    return LiveArkConfiguration(**values)


class Assembler:
    def __init__(self, evidence: L3EvidenceBundle | Exception) -> None:
        self.evidence = evidence
        self.calls: list[tuple[object, bool, str]] = []

    def assemble(self, video_id, *, privacy_reviewed, privacy_review_version):
        self.calls.append((video_id, privacy_reviewed, privacy_review_version))
        if isinstance(self.evidence, Exception):
            raise self.evidence
        return self.evidence


class Coordinator:
    def __init__(self) -> None:
        self.calls = []

    def run(self, request, *, evidence_factory, provider_factory):
        provider = provider_factory()
        self.calls.append((request, evidence_factory(), provider))
        return {"status": "completed", "execute": True, "external_calls": 1}


def test_live_service_builds_only_the_reviewed_coordinator_request() -> None:
    evidence = bundle()
    assembler = Assembler(evidence)
    coordinator = Coordinator()
    provider_kwargs = {}

    class Provider:
        provider_name = VOLCENGINE_ARK_L3_PROVIDER

    def make_provider(**kwargs):
        provider_kwargs.update(kwargs)
        return Provider()

    result = execute_reviewed_live_ark(
        "postgresql://test", selection=selection(evidence), ark=ark(),
        assembler_factory=lambda _: assembler,
        coordinator_factory=lambda _: coordinator,
        provider_factory=make_provider,
        identity_reader=lambda path: (
            "scheduled-worker" if path == AUTOMATION_WORKER_IDENTITY_PATH else ""
        ),
    )

    assert result["status"] == "completed"
    assert assembler.calls == [(evidence.video_id, True, "privacy-v1")]
    request, assembled, _ = coordinator.calls[0]
    assert assembled is evidence
    assert request.video_id == evidence.video_id
    assert request.input_fingerprint == evidence.input_fingerprint
    assert request.schema_version == L3_SCHEMA_VERSION
    assert request.provider == VOLCENGINE_ARK_L3_PROVIDER
    assert request.execute is True and request.confirmation == L3_CONFIRMATION
    assert request.actor == "scheduled-worker/l3"
    assert request.trigger_source == "schedule"
    assert request.task_key == live_ark_task_key(
        evidence.video_id,
        evidence.input_fingerprint,
        model_id=ark().model_id,
        model_revision="seed-revision-1",
        prompt_version="l3-prompt-v1",
        schema_version=L3_SCHEMA_VERSION,
    )
    assert request.estimated_llm_cost == Decimal("0.24")
    assert provider_kwargs["api_key"] == "ark-secret-not-for-output"
    assert provider_kwargs["timeout_seconds"] == 60
    assert "ark-secret-not-for-output" not in repr(result)
    assert "ark-secret-not-for-output" not in repr(ark())


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (ValueError("persisted L3 privacy review is required"), "evidence_unavailable_or_unapproved"),
        (ValueError("latest transcript evidence is required"), "evidence_unavailable_or_unapproved"),
    ],
)
def test_missing_or_unapproved_evidence_blocks_before_provider(
    failure: Exception, reason: str,
) -> None:
    evidence = bundle()
    assembler = Assembler(failure)
    coordinator = Coordinator()
    result = execute_reviewed_live_ark(
        "postgresql://test", selection=selection(evidence), ark=ark(),
        assembler_factory=lambda _: assembler,
        coordinator_factory=lambda _: coordinator,
        identity_reader=lambda _: "scheduled-worker",
    )
    assert result == {
        "status": "blocked_missing_or_stale_l3_evidence", "execute": False,
        "external_calls": 0, "llm_calls": 0, "sdk_retries": 0, "reason": reason,
    }
    assert coordinator.calls == []


def test_changed_evidence_fingerprint_blocks_before_coordinator_or_provider() -> None:
    evidence = bundle()
    coordinator = Coordinator()
    result = execute_reviewed_live_ark(
        "postgresql://test",
        selection=selection(evidence, expected_input_fingerprint="0" * 64),
        ark=ark(), assembler_factory=lambda _: Assembler(evidence),
        coordinator_factory=lambda _: coordinator,
        identity_reader=lambda _: "scheduled-worker",
    )
    assert result["reason"] == "evidence_changed_since_review"
    assert coordinator.calls == []


def test_unknown_estimate_is_forwarded_without_a_hidden_price_or_budget_cap() -> None:
    evidence = bundle()
    coordinator = Coordinator()
    execute_reviewed_live_ark(
        "postgresql://test", selection=selection(evidence, estimated_llm_cost=None),
        ark=ark(), assembler_factory=lambda _: Assembler(evidence),
        coordinator_factory=lambda _: coordinator,
        provider_factory=lambda **_: object(),
        identity_reader=lambda _: "scheduled-worker",
    )
    assert coordinator.calls[0][0].estimated_llm_cost is None


def test_selection_has_no_self_authorized_actor_or_reviewer_allowlist() -> None:
    evidence = bundle()
    with pytest.raises(TypeError):
        selection(evidence, actor="attacker@example.test")
    with pytest.raises(TypeError):
        selection(evidence, reviewer_allowlist="attacker@example.test")


def test_unavailable_or_invalid_service_identity_blocks_before_assembly_or_provider() -> None:
    evidence = bundle()
    assembler = Assembler(evidence)
    coordinator = Coordinator()
    result = execute_reviewed_live_ark(
        "postgresql://test", selection=selection(evidence), ark=ark(),
        assembler_factory=lambda _: assembler,
        coordinator_factory=lambda _: coordinator,
        identity_reader=lambda _: "\x00not-safe",
    )
    assert result["reason"] == "automation_identity_unavailable"
    assert result["external_calls"] == 0
    assert assembler.calls == []
    assert coordinator.calls == []


def test_disallowed_model_fails_before_assembly() -> None:
    evidence = bundle()
    assembler = Assembler(evidence)
    with pytest.raises(ValueError, match="qwen-flash"):
        execute_reviewed_live_ark(
            "postgresql://test", selection=selection(evidence),
            ark=ark(model_id="qwen-flash"), assembler_factory=lambda _: assembler,
        )
    assert assembler.calls == []


def test_live_service_retains_the_explicit_paid_confirmation_gate() -> None:
    evidence = bundle()
    assembler = Assembler(evidence)
    with pytest.raises(PermissionError, match="exact paid-operation confirmation"):
        execute_reviewed_live_ark(
        "postgresql://test", selection=selection(evidence, confirmation="run"),
        ark=ark(), assembler_factory=lambda _: assembler,
        )
    assert assembler.calls == []


@pytest.mark.parametrize("bad", ["NaN", "-1", object()])
def test_invalid_explicit_estimate_is_rejected(bad: object) -> None:
    evidence = bundle()
    with pytest.raises(ValueError, match="estimated LLM cost"):
        execute_reviewed_live_ark(
            "postgresql://test", selection=selection(evidence, estimated_llm_cost=bad),
            ark=ark(), assembler_factory=lambda _: Assembler(evidence),
        )


def test_same_accepted_live_inputs_have_a_stable_noncaller_task_key() -> None:
    evidence = bundle()
    one = live_ark_task_key(
        evidence.video_id, evidence.input_fingerprint,
        model_id="model", model_revision="revision", prompt_version="prompt",
        schema_version=L3_SCHEMA_VERSION,
    )
    two = live_ark_task_key(
        evidence.video_id, evidence.input_fingerprint,
        model_id="model", model_revision="revision", prompt_version="prompt",
        schema_version=L3_SCHEMA_VERSION,
    )
    changed = live_ark_task_key(
        evidence.video_id, evidence.input_fingerprint,
        model_id="model", model_revision="next-revision", prompt_version="prompt",
        schema_version=L3_SCHEMA_VERSION,
    )
    assert one == two
    assert one != changed


def test_schedule_execution_rejects_missing_actor_before_database_or_provider_work() -> None:
    request = L3ExecutionRequest(
        video_id=uuid4(), task_key="l3-scheduled-test", provider="provider",
        model_id="model", model_revision="revision", prompt_version="prompt",
        schema_version=L3_SCHEMA_VERSION, input_fingerprint="f" * 64,
        estimated_llm_cost=None, cost_currency="CNY", trigger_source="schedule",
    )
    with pytest.raises(ValueError, match="requires a trusted actor"):
        L3ExecutionCoordinator("postgresql://not-used").run(
            request,
            evidence_factory=lambda: pytest.fail("must not assemble evidence"),
            provider_factory=lambda: pytest.fail("must not build provider"),
        )
