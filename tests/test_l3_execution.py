from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from uuid import UUID

import psycopg
import pytest

from douyin_research.l0l1 import DailyBudgetGuard
from douyin_research.providers import (
    EXECUTION_CONTRACT_VERSION,
    L3_SYNC_CAPABILITY,
    VerifiedExecutionContract,
)
from douyin_research.l2 import L3PromotionGate, TaskCost
from douyin_research.l3 import (
    L3_BUDGET_KEY,
    L3_CONFIRMATION,
    L3_SCHEMA_VERSION,
    L3ExecutionCoordinator,
    L3ExecutionRequest,
    L3ProviderResponse,
    L3ResearchResult,
)


DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not configured")
BUDGET_DATE = date(2026, 9, 19)
PROVIDER = "synthetic-l3-provider"


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
    max_requests: int = 2,
    max_cost: Decimal = Decimal("1"),
    currency: str = "CNY",
) -> None:
    assert DSN
    DailyBudgetGuard(DSN).configure(
        provider=PROVIDER,
        budget_key=L3_BUDGET_KEY,
        max_requests=max_requests,
        max_cost=float(max_cost),
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
        "input_fingerprint": "synthetic-input-fingerprint",
        "estimated_llm_cost": Decimal("0.3"),
        "cost_currency": "CNY",
        "execute": False,
        "confirmation": "",
        "budget_date": BUDGET_DATE,
    }
    values.update(overrides)
    return L3ExecutionRequest(**values)


def _result(**overrides) -> L3ResearchResult:
    values = {
        "model_id": "synthetic-model",
        "model_revision": "revision-1",
        "prompt_version": "l3-prompt-v1",
        "schema_version": L3_SCHEMA_VERSION,
        "input_fingerprint": "synthetic-input-fingerprint",
        "evidence_modalities": (
            "metadata",
            "comments",
            "transcript",
            "comparison",
        ),
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


def _response(**result_overrides) -> L3ProviderResponse:
    return L3ProviderResponse(
        result=_result(**result_overrides),
        cost=TaskCost(
            api_cost=Decimal("0"),
            asr_cost=Decimal("0"),
            llm_cost=Decimal("0.21"),
            currency="CNY",
            basis="actual",
        ),
    )


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


def test_complete_is_bounded_costed_redacted_and_replay_safe() -> None:
    assert DSN
    _clear()
    video_id = _video(selected=True)
    _budget()
    provider = FakeProvider(_response())
    evidence_calls = []
    request = _request(
        video_id,
        execute=True,
        confirmation=L3_CONFIRMATION,
    )
    coordinator = L3ExecutionCoordinator(DSN)

    result = coordinator.run(
        request,
        evidence_factory=lambda: evidence_calls.append("evidence")
        or {"transcript": "private synthetic evidence"},
        provider_factory=lambda: provider,
    )
    replay_calls = []
    replay = coordinator.run(
        request,
        evidence_factory=lambda: replay_calls.append("evidence") or {},
        provider_factory=lambda: replay_calls.append("provider") or provider,
    )

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
    assert "synthetic-input-fingerprint" not in repr(result)

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select used_requests, spent_cost
            from daily_budget
            where budget_date=%s and provider=%s and budget_key=%s
            """,
            (BUDGET_DATE, PROVIDER, L3_BUDGET_KEY),
        )
        assert cur.fetchone() == (1, Decimal("0.3"))
        cur.execute(
            """
            select status, attempt_count, task_cost_id is not null,
                   metadata->>'evidence_bundle_stored'
            from l3_execution_job
            where task_key=%s
            """,
            (request.task_key,),
        )
        assert cur.fetchone() == ("completed", 1, True, "false")
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
        evidence_factory=lambda: {"metadata": "synthetic"},
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
    retrying = FakeProvider(_response())
    retrying.max_retries = 1

    with pytest.raises(ValueError, match="retries must be disabled"):
        L3ExecutionCoordinator(DSN).run(
            request,
            evidence_factory=lambda: {"metadata": "synthetic"},
            provider_factory=lambda: retrying,
        )
    assert retrying.calls == []

    invalid = FakeProvider(_response(model_revision="wrong-revision"))
    result = L3ExecutionCoordinator(DSN).run(
        request,
        evidence_factory=lambda: {"metadata": "synthetic"},
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
