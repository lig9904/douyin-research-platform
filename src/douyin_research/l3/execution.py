"""Guarded, provider-agnostic control plane for one-shot paid L3 generation."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Protocol
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from douyin_research.l2.transcripts import TaskCost
from douyin_research.providers.execution_contracts import (
    L3_SYNC_CAPABILITY,
    VerifiedExecutionContract,
    validate_execution_contract,
)

from .evidence import (
    L3EvidenceBundle,
    assert_persisted_l3_privacy_review,
    verify_l3_evidence_bundle,
)
from .results import (
    COST_BASES,
    L3_ANALYSIS_TYPE,
    L3_SCHEMA_VERSION,
    L3ResearchResult,
    L3ResearchStore,
)

L3_CONFIRMATION = "RUN_L3_MODEL_PAID"
L3_BUDGET_KEY = "windmill_manual_l3"
L3_LOCK_NAME = "douyin_research:manual_l3_execution"


class _ProviderCostExceedsReservation(Exception):
    def __init__(self, actual_cost: Decimal, cost_basis: str) -> None:
        self.actual_cost = actual_cost
        self.cost_basis = cost_basis


@dataclass(frozen=True, slots=True)
class L3ExecutionRequest:
    video_id: UUID | str
    task_key: str
    provider: str
    model_id: str
    model_revision: str
    prompt_version: str
    schema_version: str
    input_fingerprint: str
    estimated_llm_cost: Decimal | float | int | None
    cost_currency: str
    execute: bool = False
    confirmation: str = ""
    budget_date: date | None = None


@dataclass(frozen=True, slots=True)
class L3ProviderRequest:
    task_key: str
    model_id: str
    model_revision: str
    prompt_version: str
    schema_version: str
    input_fingerprint: str
    evidence_bundle: Mapping[str, object] = field(repr=False)


@dataclass(frozen=True, slots=True)
class L3ProviderResponse:
    result: L3ResearchResult = field(repr=False)
    cost: TaskCost


class L3Provider(Protocol):
    provider_name: str
    pricing_version: str
    max_retries: int
    contract: VerifiedExecutionContract

    def generate(self, request: L3ProviderRequest) -> L3ProviderResponse: ...


class L3ExecutionCoordinator:
    """Execute at most one model request after deterministic gates and reservation."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def run(
        self,
        request: L3ExecutionRequest,
        *,
        evidence_factory: Callable[[], L3EvidenceBundle],
        provider_factory: Callable[[], L3Provider],
    ) -> dict[str, object]:
        _validate_request(request)
        preview = _preview(request)
        if not request.execute:
            return preview
        if request.confirmation != L3_CONFIRMATION:
            raise PermissionError("exact paid-operation confirmation is required")
        if request.estimated_llm_cost is None:
            raise ValueError("known LLM cost estimate is required for paid execution")

        with self._single_paid_job():
            existing = self._load_job(request.task_key)
            if existing is not None:
                self._assert_job_matches(existing, request)
                reconciled = self._reconcile_terminal(request, existing)
                if reconciled is not None:
                    return reconciled
                return {
                    "status": "reconciliation_required",
                    "execute": True,
                    "created": False,
                    "external_calls": 0,
                    "attempt_count": int(existing["attempt_count"]),
                    "has_cost_record": False,
                    "error_code": existing["error_code"],
                    "sdk_retries": 0,
                    "llm_calls": 0,
                }

            self._preflight(request)
            try:
                assembled = evidence_factory()
            except Exception:
                raise RuntimeError("L3 evidence assembly failed") from None
            evidence = verify_l3_evidence_bundle(assembled)
            if assembled.video_id != UUID(str(request.video_id)):
                raise ValueError("L3 evidence video does not match execution request")
            if assembled.input_fingerprint != request.input_fingerprint:
                raise ValueError(
                    "L3 evidence fingerprint does not match execution request"
                )
            review = evidence["privacy_review"]
            assert isinstance(review, Mapping)
            with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
                cur.execute("set transaction read only")
                assert_persisted_l3_privacy_review(
                    cur,
                    assembled.video_id,
                    str(review["version"]),
                    evidence_fingerprint=assembled.input_fingerprint,
                    evidence_version=assembled.evidence_version,
                    evidence_modalities=assembled.evidence_modalities,
                )
            try:
                provider = provider_factory()
            except Exception:
                raise RuntimeError("L3 provider could not be initialized") from None
            contract_fingerprint, pricing_version = self._assert_provider(
                provider,
                request,
            )

            job = self._reserve_and_create(
                request,
                contract_fingerprint,
                pricing_version,
                assembled,
                evidence,
            )
            external_calls = 0
            try:
                external_calls += 1
                response = provider.generate(_provider_request(request, evidence))
            except Exception:
                self._mark_failed(request, UUID(str(job["id"])), "generation_failed")
                failed = self._load_job(request.task_key)
                assert failed is not None
                return self._redacted(
                    failed,
                    external_calls=external_calls,
                    created=True,
                )

            try:
                self._validate_response(
                    request,
                    response,
                    expected_modalities=assembled.evidence_modalities,
                )
                record = L3ResearchStore(self.dsn).ingest(
                    request.video_id,
                    task_key=request.task_key,
                    result=response.result,
                    cost=response.cost,
                )
                self._mark_completed(
                    UUID(str(job["id"])),
                    record.task_cost_id,
                )
            except _ProviderCostExceedsReservation as exc:
                self._mark_cost_overrun(
                    request,
                    UUID(str(job["id"])),
                    actual_cost=exc.actual_cost,
                    cost_basis=exc.cost_basis,
                )
                failed = self._load_job(request.task_key)
                assert failed is not None
                return self._redacted(
                    failed,
                    external_calls=external_calls,
                    created=True,
                )
            except Exception:
                if not self._task_cost_exists(request.task_key):
                    self._mark_failed(
                        request,
                        UUID(str(job["id"])),
                        "invalid_provider_result",
                    )
                    failed = self._load_job(request.task_key)
                    assert failed is not None
                    return self._redacted(
                        failed,
                        external_calls=external_calls,
                        created=True,
                    )
                fresh = self._load_job(request.task_key)
                assert fresh is not None
                reconciled = self._reconcile_terminal(request, fresh)
                if reconciled is not None:
                    reconciled["created"] = True
                    reconciled["external_calls"] = external_calls
                    reconciled["llm_calls"] = external_calls
                    return reconciled
                return {
                    "status": "reconciliation_required",
                    "execute": True,
                    "created": True,
                    "external_calls": external_calls,
                    "attempt_count": 1,
                    "has_cost_record": False,
                    "error_code": "result_persistence_uncertain",
                    "sdk_retries": 0,
                    "llm_calls": external_calls,
                }

            completed = self._load_job(request.task_key)
            assert completed is not None
            return self._redacted(
                completed,
                external_calls=external_calls,
                created=True,
            )

    def _preflight(self, request: L3ExecutionRequest) -> None:
        budget_date = request.budget_date or date.today()
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "select research_level from source_video where id=%s",
                (request.video_id,),
            )
            video = cur.fetchone()
            if video is None:
                raise ValueError("source video does not exist")
            if video[0] < 2:
                raise ValueError("paid L3 execution requires research level 2")

            cur.execute(
                """
                select exists(
                  select 1
                  from research_promotion_decision
                  where video_id=%s
                    and target_level=3
                    and outcome='selected'
                )
                """,
                (request.video_id,),
            )
            if not cur.fetchone()[0]:
                raise ValueError("video was not selected by the L2 to L3 gate")

            cur.execute(
                "select exists(select 1 from research_task_cost where task_key=%s)",
                (request.task_key,),
            )
            if cur.fetchone()[0]:
                raise ValueError("task_key already has a cost record without an execution job")

            cur.execute(
                """
                select max_requests, max_cost, cost_currency
                from daily_budget
                where budget_date=%s and provider=%s and budget_key=%s
                """,
                (budget_date, request.provider, L3_BUDGET_KEY),
            )
            budget = cur.fetchone()
            if budget is None or (budget[0] is None and budget[1] is None):
                raise RuntimeError("daily L3 budget must be configured")
            if budget[2] != request.cost_currency:
                raise RuntimeError("daily L3 budget currency does not match request")

    def _reserve_and_create(
        self,
        request: L3ExecutionRequest,
        contract_fingerprint: str,
        pricing_version: str,
        assembled: L3EvidenceBundle,
        evidence: Mapping[str, object],
    ) -> dict[str, object]:
        budget_date = request.budget_date or date.today()
        estimated_llm = _decimal(request.estimated_llm_cost)
        assert estimated_llm is not None
        review = evidence["privacy_review"]
        assert isinstance(review, Mapping)
        review_version = review["version"]
        job_id = uuid4()
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            _reserve_budget(
                cur,
                budget_date=budget_date,
                provider=request.provider,
                currency=request.cost_currency,
                estimated_cost=estimated_llm,
            )
            cur.execute(
                """
                insert into l3_execution_job(
                  id, task_key, video_id, provider,
                  model_id, model_revision, prompt_version, schema_version,
                  input_fingerprint, status, attempt_count,
                  estimated_llm_cost, cost_currency,
                  budget_date, budget_key, metadata
                ) values (
                  %s,%s,%s,%s,%s,%s,%s,%s,%s,
                  'running',1,%s,%s,%s,%s,%s
                )
                """,
                (
                    job_id,
                    request.task_key,
                    request.video_id,
                    request.provider,
                    request.model_id,
                    request.model_revision,
                    request.prompt_version,
                    request.schema_version,
                    request.input_fingerprint,
                    estimated_llm,
                    request.cost_currency,
                    budget_date,
                    L3_BUDGET_KEY,
                    Jsonb(
                        {
                            "manual_only": True,
                            "sdk_retries": 0,
                            "maximum_external_calls": 1,
                            "evidence_bundle_stored": False,
                            "evidence_version": assembled.evidence_version,
                            "evidence_modalities": list(
                                assembled.evidence_modalities
                            ),
                            "privacy_review_version": review_version,
                            "pricing_version": pricing_version,
                            "provider_contract_fingerprint": contract_fingerprint,
                        }
                    ),
                ),
            )
            conn.commit()
        job = self._load_job(request.task_key)
        assert job is not None
        return job

    def _mark_failed(
        self,
        request: L3ExecutionRequest,
        job_id: UUID,
        error_code: str,
    ) -> None:
        task_cost_id = self._record_failed_cost(request, error_code)
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                update l3_execution_job
                set status='failed', task_cost_id=%s,
                    error_code=%s, updated_at=now()
                where id=%s
                """,
                (task_cost_id, error_code, job_id),
            )
            conn.commit()

    def _record_failed_cost(
        self,
        request: L3ExecutionRequest,
        error_code: str,
    ) -> UUID:
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into research_task_cost(
                  task_key, task_type, task_version, video_id, status,
                  input_fingerprint, output_fingerprint,
                  api_cost, asr_cost, llm_cost,
                  cost_currency, cost_basis, metadata
                ) values (
                  %s,%s,%s,%s,'failed',%s,null,
                  0,0,null,%s,'unknown',%s
                )
                on conflict(task_key) do nothing
                returning id
                """,
                (
                    request.task_key,
                    L3_ANALYSIS_TYPE,
                    request.schema_version,
                    request.video_id,
                    request.input_fingerprint,
                    request.cost_currency,
                    Jsonb(
                        {
                            "external_execution": True,
                            "error_code": error_code,
                            "sdk_retries": 0,
                        }
                    ),
                ),
            )
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    "select id from research_task_cost where task_key=%s",
                    (request.task_key,),
                )
                row = cur.fetchone()
            conn.commit()
        assert row is not None
        return row[0]

    def _mark_cost_overrun(
        self,
        request: L3ExecutionRequest,
        job_id: UUID,
        *,
        actual_cost: Decimal,
        cost_basis: str,
    ) -> None:
        estimated = _decimal(request.estimated_llm_cost)
        assert estimated is not None and actual_cost > estimated
        budget_date = request.budget_date or date.today()
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into research_task_cost(
                  task_key, task_type, task_version, video_id, status,
                  input_fingerprint, output_fingerprint,
                  api_cost, asr_cost, llm_cost,
                  cost_currency, cost_basis, metadata
                ) values (
                  %s,%s,%s,%s,'failed',%s,null,
                  0,0,%s,%s,%s,%s
                )
                returning id
                """,
                (
                    request.task_key,
                    L3_ANALYSIS_TYPE,
                    request.schema_version,
                    request.video_id,
                    request.input_fingerprint,
                    actual_cost,
                    request.cost_currency,
                    cost_basis,
                    Jsonb(
                        {
                            "external_execution": True,
                            "error_code": "provider_cost_exceeded_reservation",
                            "reserved_llm_cost": str(estimated),
                            "actual_llm_cost": str(actual_cost),
                            "sdk_retries": 0,
                        }
                    ),
                ),
            )
            task_cost_id = cur.fetchone()[0]
            cur.execute(
                """
                update daily_budget
                set spent_cost=spent_cost+%s, updated_at=now()
                where budget_date=%s and provider=%s and budget_key=%s
                """,
                (
                    actual_cost - estimated,
                    budget_date,
                    request.provider,
                    L3_BUDGET_KEY,
                ),
            )
            cur.execute(
                """
                update l3_execution_job
                set status='failed', task_cost_id=%s,
                    error_code='provider_cost_exceeded_reservation',
                    updated_at=now()
                where id=%s
                """,
                (task_cost_id, job_id),
            )
            conn.commit()

    def _mark_completed(self, job_id: UUID, task_cost_id: UUID) -> None:
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                update l3_execution_job
                set status='completed', task_cost_id=%s,
                    error_code=null, updated_at=now()
                where id=%s
                """,
                (task_cost_id, job_id),
            )
            conn.commit()

    def _reconcile_terminal(
        self,
        request: L3ExecutionRequest,
        job: dict[str, object],
    ) -> dict[str, object] | None:
        if job["status"] in {"completed", "failed"}:
            return self._redacted(job, external_calls=0, created=False)

        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                select id, status
                from research_task_cost
                where task_key=%s
                """,
                (request.task_key,),
            )
            cost = cur.fetchone()
            if cost is None:
                return None
            task_cost_id, status = cost
            target = "completed" if status == "completed" else "failed"
            cur.execute(
                """
                update l3_execution_job
                set status=%s, task_cost_id=%s,
                    error_code=case
                      when %s='failed' then coalesce(error_code,'execution_failed')
                      else null
                    end,
                    updated_at=now()
                where id=%s
                """,
                (target, task_cost_id, target, job["id"]),
            )
            conn.commit()
        fresh = self._load_job(request.task_key)
        assert fresh is not None
        return self._redacted(fresh, external_calls=0, created=False)

    def _load_job(self, task_key: str) -> dict[str, object] | None:
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                select
                  id, task_key, video_id, provider,
                  model_id, model_revision, prompt_version, schema_version,
                  input_fingerprint, status, attempt_count,
                  estimated_llm_cost, cost_currency,
                  budget_date, budget_key, task_cost_id, error_code
                from l3_execution_job
                where task_key=%s
                """,
                (task_key,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        keys = (
            "id", "task_key", "video_id", "provider",
            "model_id", "model_revision", "prompt_version", "schema_version",
            "input_fingerprint", "status", "attempt_count",
            "estimated_llm_cost", "cost_currency",
            "budget_date", "budget_key", "task_cost_id", "error_code",
        )
        return dict(zip(keys, row, strict=True))

    def _task_cost_exists(self, task_key: str) -> bool:
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "select exists(select 1 from research_task_cost where task_key=%s)",
                (task_key,),
            )
            return bool(cur.fetchone()[0])

    @staticmethod
    def _assert_job_matches(
        job: dict[str, object],
        request: L3ExecutionRequest,
    ) -> None:
        expected = (
            UUID(str(request.video_id)),
            request.provider,
            request.model_id,
            request.model_revision,
            request.prompt_version,
            request.schema_version,
            request.input_fingerprint,
            _decimal(request.estimated_llm_cost),
            request.cost_currency,
            request.budget_date or date.today(),
            L3_BUDGET_KEY,
        )
        actual = (
            job["video_id"],
            job["provider"],
            job["model_id"],
            job["model_revision"],
            job["prompt_version"],
            job["schema_version"],
            job["input_fingerprint"],
            job["estimated_llm_cost"],
            job["cost_currency"],
            job["budget_date"],
            job["budget_key"],
        )
        if actual != expected:
            raise ValueError("task_key already exists with different L3 execution inputs")

    @staticmethod
    def _assert_provider(
        provider: L3Provider,
        request: L3ExecutionRequest,
    ) -> tuple[str, str]:
        if provider.provider_name != request.provider:
            raise ValueError("L3 provider does not match request")
        pricing_version = getattr(provider, "pricing_version", None)
        if not isinstance(pricing_version, str) or not pricing_version.strip():
            raise ValueError("L3 provider pricing version is required")
        if provider.max_retries != 0:
            raise ValueError("L3 provider retries must be disabled")
        if provider.contract.max_retries != provider.max_retries:
            raise ValueError("L3 provider retry settings do not match contract")
        fingerprint = validate_execution_contract(
            provider.contract,
            expected_provider=request.provider,
            expected_capability=L3_SYNC_CAPABILITY,
            expected_model_id=request.model_id,
            expected_model_revision=request.model_revision,
            expected_currency=request.cost_currency,
        )
        return fingerprint, pricing_version.strip()

    @staticmethod
    def _validate_response(
        request: L3ExecutionRequest,
        response: L3ProviderResponse,
        *,
        expected_modalities: tuple[str, ...],
    ) -> None:
        result = response.result
        actual = (
            result.model_id,
            result.model_revision,
            result.prompt_version,
            result.schema_version,
            result.input_fingerprint,
        )
        expected = (
            request.model_id,
            request.model_revision,
            request.prompt_version,
            request.schema_version,
            request.input_fingerprint,
        )
        if actual != expected:
            raise ValueError("L3 provider result does not match execution request")
        if set(result.evidence_modalities) != set(expected_modalities):
            raise ValueError("L3 provider evidence modalities do not match input")

        cost = response.cost
        if cost.currency != request.cost_currency:
            raise ValueError("L3 provider cost currency does not match request")
        values = tuple(
            None if value is None else Decimal(str(value))
            for value in (cost.api_cost, cost.asr_cost, cost.llm_cost)
        )
        if any(value is not None and value < 0 for value in values):
            raise ValueError("L3 provider costs cannot be negative")
        if values[0] != Decimal("0") or values[1] != Decimal("0"):
            raise ValueError("L3 model execution requires api_cost=0 and asr_cost=0")
        if cost.basis not in COST_BASES:
            raise ValueError("unsupported L3 provider cost basis")
        estimated = _decimal(request.estimated_llm_cost)
        assert estimated is not None
        if values[2] is not None and values[2] > estimated:
            raise _ProviderCostExceedsReservation(values[2], cost.basis)

    @contextmanager
    def _single_paid_job(self) -> Iterator[None]:
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "select pg_try_advisory_lock(hashtext(%s))",
                (L3_LOCK_NAME,),
            )
            if not cur.fetchone()[0]:
                raise RuntimeError("another manual L3 execution is already running")
            try:
                yield
            finally:
                cur.execute(
                    "select pg_advisory_unlock(hashtext(%s))",
                    (L3_LOCK_NAME,),
                )

    @staticmethod
    def _redacted(
        job: dict[str, object],
        *,
        external_calls: int,
        created: bool,
    ) -> dict[str, object]:
        return {
            "status": job["status"],
            "execute": True,
            "created": created,
            "external_calls": external_calls,
            "attempt_count": int(job["attempt_count"]),
            "has_cost_record": job["task_cost_id"] is not None,
            "error_code": job["error_code"],
            "sdk_retries": 0,
            "llm_calls": external_calls,
        }


def _preview(request: L3ExecutionRequest) -> dict[str, object]:
    estimated = _decimal(request.estimated_llm_cost)
    return {
        "status": "preview",
        "execute": False,
        "maximum_external_calls": 1,
        "maximum_llm_calls": 1,
        "estimated_total_cost": float(estimated) if estimated is not None else None,
        "cost_currency": request.cost_currency,
        "execution_ready": estimated is not None,
        "sdk_retries": 0,
        "llm_calls": 0,
    }


def _validate_request(request: L3ExecutionRequest) -> None:
    required = {
        "task_key": request.task_key,
        "provider": request.provider,
        "model_id": request.model_id,
        "model_revision": request.model_revision,
        "prompt_version": request.prompt_version,
        "schema_version": request.schema_version,
        "input_fingerprint": request.input_fingerprint,
        "cost_currency": request.cost_currency,
    }
    missing = [name for name, value in required.items() if not str(value).strip()]
    if missing:
        raise ValueError(f"required L3 execution fields are missing: {missing}")
    if request.schema_version != L3_SCHEMA_VERSION:
        raise ValueError("unsupported L3 schema version")
    estimated = _decimal(request.estimated_llm_cost)
    if estimated is not None and estimated < 0:
        raise ValueError("estimated LLM cost cannot be negative")


def _provider_request(
    request: L3ExecutionRequest,
    evidence: Mapping[str, object],
) -> L3ProviderRequest:
    return L3ProviderRequest(
        task_key=request.task_key,
        model_id=request.model_id,
        model_revision=request.model_revision,
        prompt_version=request.prompt_version,
        schema_version=request.schema_version,
        input_fingerprint=request.input_fingerprint,
        evidence_bundle=evidence,
    )


def _reserve_budget(
    cur,
    *,
    budget_date: date,
    provider: str,
    currency: str,
    estimated_cost: Decimal,
) -> None:
    cur.execute(
        """
        select max_cost, max_requests, spent_cost, used_requests, cost_currency
        from daily_budget
        where budget_date=%s and provider=%s and budget_key=%s
        for update
        """,
        (budget_date, provider, L3_BUDGET_KEY),
    )
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("daily L3 budget must be configured")
    max_cost, max_requests, spent_cost, used_requests, configured_currency = row
    if configured_currency != currency:
        raise RuntimeError("daily L3 budget currency does not match request")
    next_requests = used_requests + 1
    next_cost = Decimal(spent_cost) + estimated_cost
    if max_requests is not None and next_requests > max_requests:
        raise RuntimeError("daily L3 request budget exceeded")
    if max_cost is not None and next_cost > Decimal(max_cost):
        raise RuntimeError("daily L3 cost budget exceeded")
    cur.execute(
        """
        update daily_budget
        set used_requests=%s, spent_cost=%s, updated_at=now()
        where budget_date=%s and provider=%s and budget_key=%s
        """,
        (next_requests, next_cost, budget_date, provider, L3_BUDGET_KEY),
    )


def _decimal(value: Decimal | float | int | None) -> Decimal | None:
    if value is None:
        return None
    parsed = Decimal(str(value))
    if not parsed.is_finite():
        raise ValueError("cost must be finite")
    return parsed
