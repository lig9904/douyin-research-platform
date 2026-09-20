from __future__ import annotations

import os
from datetime import date, timedelta
from dataclasses import replace
from decimal import Decimal
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from douyin_research.l0l1 import DailyBudgetGuard
from douyin_research.l2 import L3PromotionGate, TaskCost
from douyin_research.l3 import (
    L3_BUDGET_KEY,
    L3_CONFIRMATION,
    L3_EVIDENCE_VERSION,
    L3_SCHEMA_VERSION,
    L3EvidenceBundle,
    L3ExecutionCoordinator,
    L3ExecutionRequest,
    L3ProviderResponse,
    L3ResearchResult,
)
from douyin_research.providers import (
    EXECUTION_CONTRACT_VERSION,
    L3_SYNC_CAPABILITY,
    VerifiedExecutionContract,
)

DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not configured")
BUDGET_DATE = date(2026, 9, 19)
PROVIDER = "synthetic-l3-provider"
SYNTHETIC_MODALITIES = ("metadata", "comments", "transcript", "comparison")


def _evidence_payload(payload=None, *, modalities=SYNTHETIC_MODALITIES):
    return {
        "evidence_version": L3_EVIDENCE_VERSION,
        "modalities": list(modalities),
        "privacy_review": {"reviewed": True, "version": "privacy-v1"},
        "synthetic_payload": (
            {"metadata": "synthetic"} if payload is None else payload
        ),
    }


SYNTHETIC_INPUT_FINGERPRINT = L3EvidenceBundle.issue(
    video_id=UUID(int=0),
    evidence_bundle=_evidence_payload(),
).input_fingerprint


def _clear() -> None:
    assert DSN
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("delete from l3_execution_job")
        cur.execute("delete from analysis_run")
        cur.execute("delete from research_task_cost")
        cur.execute("delete from research_promotion_decision")
        cur.execute("delete from research_promotion_batch")
        cur.execute("delete from daily_research_quota")
        cur.execute("delete from pipeline_run")
        cur.execute("delete from daily_budget")
        cur.execute("delete from source_video")
        conn.commit()


def _video(*, selected: bool, key: str = "main") -> UUID:
    assert DSN
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into pipeline_run(run_type, run_version, platform, status)
            values ('synthetic-source', 'test', 'douyin', 'success')
            returning id
            """
        )
        source_run_id = cur.fetchone()[0]
        cur.execute(
            """
            insert into source_video(platform, platform_video_id, research_level)
            values ('douyin', %s, 2)
            returning id
            """,
            (f"synthetic-l3-execution-{key}",),
        )
        video_id = cur.fetchone()[0]
        cur.execute(
            """
            insert into human_annotation(
              video_id, actor, annotation_type, value
            ) values (
              %s, 'synthetic-reviewer', 'l3_privacy_review', %s
            )
            """,
            (
                video_id,
                Jsonb(
                    {
                        "reviewed": True,
                        "version": "privacy-v1",
                        "evidence_fingerprint": SYNTHETIC_INPUT_FINGERPRINT,
                        "evidence_version": L3_EVIDENCE_VERSION,
                        "evidence_modalities": list(SYNTHETIC_MODALITIES),
                        "reviewer_identity_source": "windmill_end_user_email_allowlist_v1",
                    }
                ),
            ),
        )
        cur.execute(
            """
            insert into pipeline_run_item(
              run_id, entity_type, entity_id, stage, outcome
            ) values (%s, 'video', %s, 'L2', 'scored')
            """,
            (source_run_id, video_id),
        )
        cur.execute(
            """
            insert into video_score(video_id, score_type, score, rule_version)
            values (%s, 'priority', 90, 'synthetic-v1')
            """,
            (video_id,),
        )
        conn.commit()

    if selected:
        gate = L3PromotionGate(DSN)
        gate.configure_daily_quota(
            platform="douyin",
            max_items=1,
            quota_date=BUDGET_DATE,
        )
        gate.promote(
            source_run_id,
            top_n=1,
            quota_date=BUDGET_DATE,
        )
    return video_id


def _budget(
    *,
    max_requests: int | None = 2,
    max_cost: Decimal | None = Decimal("1"),
    currency: str = "CNY",
) -> None:
    assert DSN
    DailyBudgetGuard(DSN).configure(
        provider=PROVIDER,
        budget_key=L3_BUDGET_KEY,
        max_requests=max_requests,
        max_cost=float(max_cost) if max_cost is not None else None,
        budget_date=BUDGET_DATE,
        cost_currency=currency,
    )


def _request(video_id: UUID, **overrides) -> L3ExecutionRequest:
    values = {
        "video_id": video_id,
        "task_key": "synthetic-l3-execution-task",
        "provider": PROVIDER,
        "model_id": "synthetic-model",
        "model_revision": "revision-1",
        "prompt_version": "l3-prompt-v1",
        "schema_version": L3_SCHEMA_VERSION,
        "input_fingerprint": SYNTHETIC_INPUT_FINGERPRINT,
        "estimated_llm_cost": Decimal("0.3"),
        "cost_currency": "CNY",
        "execute": False,
        "confirmation": "",
        "budget_date": BUDGET_DATE,
    }
    values.update(overrides)
    return L3ExecutionRequest(**values)


def test_scheduled_execution_requires_a_trusted_actor_before_any_database_call() -> None:
    request = _request(uuid4(), trigger_source="schedule")
    with pytest.raises(ValueError, match="requires a trusted actor"):
        L3ExecutionCoordinator("postgresql://not-used").run(
            request,
            evidence_factory=lambda: pytest.fail("evidence must not be assembled"),
            provider_factory=lambda: pytest.fail("provider must not be built"),
        )


def test_schedule_actor_metadata_validation_accepts_a_bounded_service_identity() -> None:
    request = _request(
        uuid4(), execute=False, trigger_source="schedule", actor="windmill-worker/l3"
    )
    preview = L3ExecutionCoordinator("postgresql://not-used").run(
        request,
        evidence_factory=lambda: pytest.fail("preview must not assemble evidence"),
        provider_factory=lambda: pytest.fail("preview must not build provider"),
    )
    assert preview["status"] == "preview"


def _result(**overrides) -> L3ResearchResult:
    values = {
        "model_id": "synthetic-model",
        "model_revision": "revision-1",
        "prompt_version": "l3-prompt-v1",
        "schema_version": L3_SCHEMA_VERSION,
        "input_fingerprint": SYNTHETIC_INPUT_FINGERPRINT,
        "evidence_modalities": SYNTHETIC_MODALITIES,
        "narrative_structure": ("Synthetic narrative structure.",),
        "hook_functions": ("Synthetic information gap.",),
        "comment_semantics": ("Synthetic sampled discussion pattern.",),
        "case_comparisons": ("Synthetic comparison finding.",),
        "mechanism_hypotheses": ("Synthetic mechanism hypothesis.",),
        "ip_fit": ("Synthetic adaptation recommendation.",),
        "limitations": ("Bounded synthetic evidence.",),
        "privacy_reviewed": True,
    }
    values.update(overrides)
    return L3ResearchResult(**values)


def _provider_contract() -> VerifiedExecutionContract:
    return VerifiedExecutionContract(
        provider=PROVIDER,
        capability=L3_SYNC_CAPABILITY,
        contract_version=EXECUTION_CONTRACT_VERSION,
        model_id="synthetic-model",
        model_revision="revision-1",
        base_url="https://relay.example.test/v1",
        auth_scheme="bearer",
        auth_header_name="Authorization",
        submit_path="/responses",
        status_path=None,
        request_schema_version="l3-request-v1",
        response_schema_version="l3-response-v1",
        cost_currency="CNY",
        polling_billed=False,
        max_retries=0,
        timeout_seconds=30,
        verified_source_fingerprint="b" * 64,
        production_ready=True,
    )


class FakeProvider:
    provider_name = PROVIDER
    pricing_version = "synthetic-pricing-v1"
    max_retries = 0

    def __init__(self, response):
        self.contract = _provider_contract()
        self.response = response
        self.calls = []

    def generate(self, request):
        self.calls.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _response(*, cost: TaskCost | None = None, **result_overrides) -> L3ProviderResponse:
    return L3ProviderResponse(
        result=_result(**result_overrides),
        cost=cost
        or TaskCost(
            api_cost=Decimal("0"),
            asr_cost=Decimal("0"),
            llm_cost=Decimal("0.21"),
            currency="CNY",
            basis="actual",
        ),
    )


def _evidence(
    video_id: UUID,
    payload=None,
    *,
    fingerprint=None,
    modalities=SYNTHETIC_MODALITIES,
) -> L3EvidenceBundle:
    issued = L3EvidenceBundle.issue(
        video_id=video_id,
        evidence_bundle=_evidence_payload(payload, modalities=modalities),
    )
    if fingerprint is None:
        return issued
    return L3EvidenceBundle(
        video_id=issued.video_id,
        evidence_version=issued.evidence_version,
        input_fingerprint=fingerprint,
        evidence_modalities=issued.evidence_modalities,
        evidence_bundle=issued.evidence_bundle,
    )


def _persist_review(video_id: UUID, evidence: L3EvidenceBundle) -> None:
    assert DSN
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into human_annotation(
              video_id, actor, annotation_type, value
            ) values (%s, 'synthetic-reviewer', 'l3_privacy_review', %s)
            """,
            (
                video_id,
                Jsonb(
                    {
                        "reviewed": True,
                        "version": "privacy-v1",
                        "evidence_fingerprint": evidence.input_fingerprint,
                        "evidence_version": evidence.evidence_version,
                        "evidence_modalities": list(evidence.evidence_modalities),
                        "reviewer_identity_source": "windmill_end_user_email_allowlist_v1",
                    }
                ),
            ),
        )
        conn.commit()


def test_preview_and_wrong_confirmation_load_nothing() -> None:
    assert DSN
    _clear()
    video_id = _video(selected=False)
    coordinator = L3ExecutionCoordinator(DSN)
    calls = []

    preview = coordinator.run(
        _request(video_id),
        evidence_factory=lambda: calls.append("evidence") or {},
        provider_factory=lambda: calls.append("provider") or None,
    )

    assert calls == []
    assert preview == {
        "status": "preview",
        "execute": False,
        "maximum_external_calls": 1,
        "maximum_llm_calls": 1,
        "estimated_total_cost": 0.3,
        "cost_currency": "CNY",
        "execution_ready": True,
        "price_known": True,
        "sdk_retries": 0,
        "llm_calls": 0,
    }
    assert "video_id" not in preview
    assert "task_key" not in preview

    with pytest.raises(PermissionError, match="exact paid-operation confirmation"):
        coordinator.run(
            _request(video_id, execute=True, confirmation="yes"),
            evidence_factory=lambda: calls.append("evidence") or {},
            provider_factory=lambda: calls.append("provider") or None,
        )
    assert calls == []


def test_gate_and_budget_fail_before_evidence_or_provider() -> None:
    assert DSN
    _clear()
    coordinator = L3ExecutionCoordinator(DSN)
    calls = []
    unselected = _video(selected=False)

    with pytest.raises(ValueError, match="not selected"):
        coordinator.run(
            _request(
                unselected,
                execute=True,
                confirmation=L3_CONFIRMATION,
            ),
            evidence_factory=lambda: calls.append("evidence") or {"x": 1},
            provider_factory=lambda: calls.append("provider") or None,
        )
    assert calls == []

    _clear()
    selected = _video(selected=True, key="selected-no-budget")
    with pytest.raises(RuntimeError, match="budget must be configured"):
        coordinator.run(
            _request(
                selected,
                execute=True,
                confirmation=L3_CONFIRMATION,
            ),
            evidence_factory=lambda: calls.append("evidence") or {"x": 1},
            provider_factory=lambda: calls.append("provider") or None,
        )
    assert calls == []


def test_complete_is_bounded_costed_redacted_and_replay_safe(monkeypatch) -> None:
    assert DSN
    _clear()
    video_id = _video(selected=True)
    _budget()
    assembled = _evidence(
        video_id,
        {"transcript": "private synthetic evidence"},
    )
    _persist_review(video_id, assembled)
    provider = FakeProvider(
        _response(input_fingerprint=assembled.input_fingerprint)
    )
    evidence_calls = []
    request = _request(
        video_id,
        execute=True,
        confirmation=L3_CONFIRMATION,
        input_fingerprint=assembled.input_fingerprint,
    )
    coordinator = L3ExecutionCoordinator(DSN)

    result = coordinator.run(
        request,
        evidence_factory=lambda: (
            evidence_calls.append("evidence")
            or assembled
        ),
        provider_factory=lambda: provider,
    )
    replay_calls = []
    class NextDay(date):
        @classmethod
        def today(cls):
            return BUDGET_DATE + timedelta(days=1)
    monkeypatch.setattr("douyin_research.l3.execution.date", NextDay)
    replay = coordinator.run(
        replace(request, budget_date=None),
        evidence_factory=lambda: replay_calls.append("evidence") or {},
        provider_factory=lambda: replay_calls.append("provider") or provider,
    )
    with pytest.raises(ValueError, match="different L3 execution inputs"):
        coordinator.run(replace(request, budget_date=NextDay.today()),
            evidence_factory=lambda: pytest.fail("unexpected evidence read"),
            provider_factory=lambda: pytest.fail("unexpected paid call"))

    assert result["status"] == "completed"
    assert result["external_calls"] == 1
    assert result["attempt_count"] == 1
    assert result["has_cost_record"] is True
    assert result["llm_calls"] == 1
    assert replay["status"] == "completed"
    assert replay["external_calls"] == 0
    assert replay["llm_calls"] == 0
    assert replay_calls == []
    assert evidence_calls == ["evidence"]
    assert len(provider.calls) == 1
    assert "private synthetic evidence" not in repr(provider.calls[0])
    assert assembled.input_fingerprint not in repr(result)

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select used_requests, spent_cost
            from daily_budget
            where budget_date=%s and provider=%s and budget_key=%s
            """,
            (BUDGET_DATE, PROVIDER, L3_BUDGET_KEY),
        )
        assert cur.fetchone() == (1, Decimal("0.21"))
        cur.execute(
            """
            select status, attempt_count, task_cost_id is not null,
                   metadata->>'evidence_bundle_stored',
                   metadata->>'evidence_version',
                   metadata->>'privacy_review_version',
                   metadata->>'pricing_version',
                   metadata->'evidence_modalities'
            from l3_execution_job
            where task_key=%s
            """,
            (request.task_key,),
        )
        assert cur.fetchone() == (
            "completed",
            1,
            True,
            "false",
            L3_EVIDENCE_VERSION,
            "privacy-v1",
            "synthetic-pricing-v1",
            list(SYNTHETIC_MODALITIES),
        )
        cur.execute(
            """
            select api_cost, asr_cost, llm_cost, total_cost, status
            from research_task_cost
            where task_key=%s
            """,
            (request.task_key,),
        )
        assert cur.fetchone() == (
            Decimal("0"),
            Decimal("0"),
            Decimal("0.21"),
            Decimal("0.21"),
            "completed",
        )


def test_execution_requires_evidence_object_bound_to_video_and_fingerprint() -> None:
    assert DSN
    _clear()
    video_id = _video(selected=True, key="evidence-binding")
    _budget()
    request = _request(
        video_id,
        execute=True,
        confirmation=L3_CONFIRMATION,
        task_key="synthetic-l3-evidence-binding",
    )
    coordinator = L3ExecutionCoordinator(DSN)
    provider_calls = []

    with pytest.raises(TypeError, match="must return L3EvidenceBundle"):
        coordinator.run(
            request,
            evidence_factory=lambda: {"metadata": "unbound"},
            provider_factory=lambda: provider_calls.append("provider"),
        )
    with pytest.raises(ValueError, match="fingerprint does not match"):
        coordinator.run(
            request,
            evidence_factory=lambda: _evidence(
                video_id,
                fingerprint="different-fingerprint",
            ),
            provider_factory=lambda: provider_calls.append("provider"),
        )
    tampered = _evidence(video_id)
    tampered.evidence_bundle["synthetic_payload"] = {"metadata": "tampered"}
    with pytest.raises(ValueError, match="fingerprint does not match payload"):
        coordinator.run(
            request,
            evidence_factory=lambda: tampered,
            provider_factory=lambda: provider_calls.append("provider"),
        )
    with pytest.raises(ValueError, match="video does not match"):
        coordinator.run(
            request,
            evidence_factory=lambda: _evidence(UUID(int=0)),
            provider_factory=lambda: provider_calls.append("provider"),
        )
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            update human_annotation
            set value=jsonb_set(value, '{evidence_fingerprint}', '"stale"')
            where video_id=%s and annotation_type='l3_privacy_review'
            """,
            (video_id,),
        )
        conn.commit()
    with pytest.raises(ValueError, match="does not match evidence"):
        coordinator.run(
            request,
            evidence_factory=lambda: _evidence(video_id),
            provider_factory=lambda: provider_calls.append("provider"),
        )
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "delete from human_annotation where video_id=%s",
            (video_id,),
        )
        conn.commit()
    with pytest.raises(ValueError, match="persisted L3 privacy review"):
        coordinator.run(
            request,
            evidence_factory=lambda: _evidence(video_id),
            provider_factory=lambda: provider_calls.append("provider"),
        )

    assert provider_calls == []
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select used_requests, spent_cost
            from daily_budget
            where budget_date=%s and provider=%s and budget_key=%s
            """,
            (BUDGET_DATE, PROVIDER, L3_BUDGET_KEY),
        )
        assert cur.fetchone() == (0, Decimal("0"))
        cur.execute("select count(*) from l3_execution_job")
        assert cur.fetchone()[0] == 0


def test_generation_failure_is_not_retried_and_unknown_cost_stays_null() -> None:
    assert DSN
    _clear()
    video_id = _video(selected=True, key="failure")
    _budget()
    provider = FakeProvider(RuntimeError("secret upstream payload"))
    request = _request(
        video_id,
        execute=True,
        confirmation=L3_CONFIRMATION,
        task_key="synthetic-l3-failure-task",
    )
    coordinator = L3ExecutionCoordinator(DSN)

    result = coordinator.run(
        request,
        evidence_factory=lambda: _evidence(video_id),
        provider_factory=lambda: provider,
    )
    replay_calls = []
    replay = coordinator.run(
        request,
        evidence_factory=lambda: replay_calls.append("evidence") or {},
        provider_factory=lambda: replay_calls.append("provider") or provider,
    )

    assert result["status"] == "failed"
    assert result["error_code"] == "generation_failed"
    assert result["external_calls"] == 1
    assert result["llm_calls"] == 1
    assert replay["external_calls"] == 0
    assert replay_calls == []
    assert len(provider.calls) == 1
    assert "secret upstream payload" not in repr(result)

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select api_cost, asr_cost, llm_cost, total_cost, status
            from research_task_cost
            where task_key=%s
            """,
            (request.task_key,),
        )
        assert cur.fetchone() == (
            Decimal("0"),
            Decimal("0"),
            None,
            None,
            "failed",
        )


def test_provider_modalities_cannot_claim_unsupplied_evidence() -> None:
    assert DSN
    _clear()
    video_id = _video(selected=True, key="modality-mismatch")
    _budget()
    provider = FakeProvider(
        _response(evidence_modalities=("metadata",))
    )
    result = L3ExecutionCoordinator(DSN).run(
        _request(
            video_id,
            execute=True,
            confirmation=L3_CONFIRMATION,
            task_key="synthetic-l3-modality-mismatch",
        ),
        evidence_factory=lambda: _evidence(video_id),
        provider_factory=lambda: provider,
    )

    assert result["status"] == "failed"
    assert result["error_code"] == "invalid_provider_result"
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("select count(*) from analysis_run")
        assert cur.fetchone()[0] == 0


def test_actual_cost_over_reservation_is_recorded_and_fails_closed(monkeypatch) -> None:
    assert DSN
    _clear()
    video_id = _video(selected=True, key="cost-overrun")
    _budget(max_cost=Decimal("0.4"))
    class Clock(date):
        current = BUDGET_DATE
        @classmethod
        def today(cls):
            return cls.current
    monkeypatch.setattr("douyin_research.l3.execution.date", Clock)
    provider = FakeProvider(
        _response(
            cost=TaskCost(
                api_cost=0,
                asr_cost=0,
                llm_cost=Decimal("0.45"),
                currency="CNY",
                basis="actual",
            )
        )
    )
    original_generate = provider.generate
    def across_midnight(request):
        Clock.current = BUDGET_DATE + timedelta(days=1)
        return original_generate(request)
    provider.generate = across_midnight
    result = L3ExecutionCoordinator(DSN).run(
        _request(
            video_id,
            execute=True,
            confirmation=L3_CONFIRMATION,
            task_key="synthetic-l3-cost-overrun",
            budget_date=None,
        ),
        evidence_factory=lambda: _evidence(video_id),
        provider_factory=lambda: provider,
    )

    assert result["status"] == "failed"
    assert result["error_code"] == "provider_cost_exceeded_reservation"
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select used_requests, spent_cost
            from daily_budget
            where budget_date=%s and provider=%s and budget_key=%s
            """,
            (BUDGET_DATE, PROVIDER, L3_BUDGET_KEY),
        )
        assert cur.fetchone() == (1, Decimal("0.45"))
        cur.execute(
            """
            select status, api_cost, asr_cost, llm_cost, total_cost,
                   metadata->>'reserved_llm_cost',
                   metadata->>'actual_llm_cost'
            from research_task_cost
            where task_key='synthetic-l3-cost-overrun'
            """
        )
        assert cur.fetchone() == (
            "failed",
            Decimal("0"),
            Decimal("0"),
            Decimal("0.45"),
            Decimal("0.45"),
            "0.3",
            "0.45",
        )
        cur.execute("select count(*) from analysis_run")
        assert cur.fetchone()[0] == 0


def test_running_job_requires_reconciliation_without_second_call() -> None:
    assert DSN
    _clear()
    video_id = _video(selected=True, key="reconciliation")
    request = _request(
        video_id,
        execute=True,
        confirmation=L3_CONFIRMATION,
        task_key="synthetic-l3-running-task",
    )
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into l3_execution_job(
              id, task_key, video_id, provider,
              model_id, model_revision, prompt_version, schema_version,
              input_fingerprint, status, attempt_count,
              estimated_llm_cost, cost_currency, budget_date, budget_key
            ) values (
              gen_random_uuid(),%s,%s,%s,%s,%s,%s,%s,%s,
              'running',1,%s,%s,%s,%s
            )
            """,
            (
                request.task_key,
                request.video_id,
                request.provider,
                request.model_id,
                request.model_revision,
                request.prompt_version,
                request.schema_version,
                request.input_fingerprint,
                request.estimated_llm_cost,
                request.cost_currency,
                request.budget_date,
                L3_BUDGET_KEY,
            ),
        )
        conn.commit()

    calls = []
    result = L3ExecutionCoordinator(DSN).run(
        request,
        evidence_factory=lambda: calls.append("evidence") or {},
        provider_factory=lambda: calls.append("provider") or None,
    )

    assert result["status"] == "reconciliation_required"
    assert result["external_calls"] == 0
    assert calls == []


def test_retrying_provider_and_invalid_result_are_rejected_safely() -> None:
    assert DSN
    _clear()
    video_id = _video(selected=True, key="provider-guard")
    _budget()
    request = _request(
        video_id,
        execute=True,
        confirmation=L3_CONFIRMATION,
        task_key="synthetic-l3-provider-guard",
    )
    unpriced = FakeProvider(_response())
    unpriced.pricing_version = ""
    with pytest.raises(ValueError, match="pricing version is required"):
        L3ExecutionCoordinator(DSN).run(
            request,
            evidence_factory=lambda: _evidence(video_id),
            provider_factory=lambda: unpriced,
        )
    assert unpriced.calls == []

    retrying = FakeProvider(_response())
    retrying.max_retries = 1

    with pytest.raises(ValueError, match="retries must be disabled"):
        L3ExecutionCoordinator(DSN).run(
            request,
            evidence_factory=lambda: _evidence(video_id),
            provider_factory=lambda: retrying,
        )
    assert retrying.calls == []

    invalid = FakeProvider(_response(model_revision="wrong-revision"))
    result = L3ExecutionCoordinator(DSN).run(
        request,
        evidence_factory=lambda: _evidence(video_id),
        provider_factory=lambda: invalid,
    )
    assert result["status"] == "failed"
    assert result["error_code"] == "invalid_provider_result"
    assert result["external_calls"] == 1

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select used_requests
            from daily_budget
            where budget_date=%s and provider=%s and budget_key=%s
            """,
            (BUDGET_DATE, PROVIDER, L3_BUDGET_KEY),
        )
        assert cur.fetchone()[0] == 1


def test_unknown_price_reservation_is_explicit_and_reconciles_known_usage_cost(monkeypatch) -> None:
    assert DSN
    _clear()
    video_id = _video(selected=True, key="unknown-live-price")
    _budget(max_requests=None, max_cost=None)
    provider = FakeProvider(_response())
    class Clock(date):
        current = BUDGET_DATE
        @classmethod
        def today(cls):
            return cls.current
    monkeypatch.setattr("douyin_research.l3.execution.date", Clock)
    original_generate = provider.generate
    def across_midnight(request):
        Clock.current = BUDGET_DATE + timedelta(days=1)
        return original_generate(request)
    provider.generate = across_midnight
    request = _request(
        video_id,
        execute=True,
        confirmation=L3_CONFIRMATION,
        estimated_llm_cost=None,
        task_key="synthetic-l3-unknown-live-price",
        budget_date=None,
    )

    result = L3ExecutionCoordinator(DSN).run(
        request,
        evidence_factory=lambda: _evidence(video_id),
        provider_factory=lambda: provider,
    )

    assert result["status"] == "completed"
    assert len(provider.calls) == 1
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select used_requests, unknown_price_requests, spent_cost
            from daily_budget
            where budget_date=%s and provider=%s and budget_key=%s
            """,
            (BUDGET_DATE, PROVIDER, L3_BUDGET_KEY),
        )
        assert cur.fetchone() == (1, 0, Decimal("0.21"))
    # Simulate a crash between atomic budget settlement and completed status.
    with psycopg.connect(DSN) as conn:
        conn.execute("update l3_execution_job set status='running' where task_key=%s", (request.task_key,))
    replay = L3ExecutionCoordinator(DSN).run(request,
        evidence_factory=lambda: pytest.fail("recovery must reuse evidence"),
        provider_factory=lambda: pytest.fail("recovery must not call provider"))
    assert replay["status"] == "completed"
    with psycopg.connect(DSN) as conn:
        assert conn.execute("select spent_cost from daily_budget where provider=%s and budget_key=%s", (PROVIDER, L3_BUDGET_KEY)).fetchone() == (Decimal("0.21"),)


def test_persisted_result_waits_for_budget_reconciliation_without_regeneration(monkeypatch):
    _clear()
    video = _video(selected=True, key="budget-recovery")
    _budget(max_requests=None, max_cost=None)
    provider = FakeProvider(_response())
    coordinator = L3ExecutionCoordinator(DSN)
    request = _request(video, execute=True, confirmation=L3_CONFIRMATION, estimated_llm_cost=None)
    real_reconcile = coordinator._reconcile_completed_budget
    def unavailable(*args):
        raise RuntimeError("synthetic ledger outage")
    monkeypatch.setattr(coordinator, "_reconcile_completed_budget", unavailable)
    first = coordinator.run(request, evidence_factory=lambda: _evidence(video), provider_factory=lambda: provider)
    assert first["status"] == "reconciliation_required"
    with psycopg.connect(DSN) as conn:
        assert conn.execute("select status from l3_execution_job where task_key=%s", (request.task_key,)).fetchone()[0] != "completed"
        assert conn.execute("select status from research_task_cost where task_key=%s", (request.task_key,)).fetchone()[0] == "completed"
    monkeypatch.setattr(coordinator, "_reconcile_completed_budget", real_reconcile)
    result = coordinator.run(request, evidence_factory=lambda: pytest.fail("no new evidence"),
                             provider_factory=lambda: pytest.fail("no paid resubmit"))
    assert result["status"] == "completed"
    assert len(provider.calls) == 1
    with psycopg.connect(DSN) as conn:
        assert conn.execute("select unknown_price_requests,spent_cost from daily_budget where provider=%s and budget_key=%s", (PROVIDER, L3_BUDGET_KEY)).fetchone() == (0, Decimal("0.21"))


def test_unknown_price_is_rejected_when_the_configured_budget_has_a_cost_ceiling() -> None:
    assert DSN
    _clear()
    video_id = _video(selected=True, key="unknown-price-ceiling")
    _budget(max_requests=None, max_cost=Decimal("1"))
    calls = []
    with pytest.raises(RuntimeError, match="unknown L3 price"):
        L3ExecutionCoordinator(DSN).run(
            _request(
                video_id,
                execute=True,
                confirmation=L3_CONFIRMATION,
                estimated_llm_cost=None,
                task_key="synthetic-l3-unknown-price-ceiling",
            ),
            evidence_factory=lambda: calls.append("evidence") or _evidence(video_id),
            provider_factory=lambda: calls.append("provider") or FakeProvider(_response()),
        )
    assert calls == []
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select used_requests, unknown_price_requests, spent_cost
            from daily_budget
            where budget_date=%s and provider=%s and budget_key=%s
            """,
            (BUDGET_DATE, PROVIDER, L3_BUDGET_KEY),
        )
        assert cur.fetchone() == (0, 0, Decimal("0"))
