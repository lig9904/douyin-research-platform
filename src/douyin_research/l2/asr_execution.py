"""Guarded, provider-agnostic control plane for paid ASR execution."""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Callable, Iterator, Protocol
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from .transcripts import (
    ASR_EVIDENCE_VERSION,
    TaskCost,
    TranscriptEvidence,
    TranscriptEvidenceStore,
)


ASR_CONFIRMATION = "RUN_ASR_PAID"
ASR_BUDGET_KEY = "windmill_manual_asr"
ASR_LOCK_NAME = "douyin_research:manual_asr_execution"
MAX_POLLS_PER_RUN = 3
ALLOWED_PROVIDER_STATUSES = frozenset(
    {"submitted", "running", "completed", "failed"}
)


@dataclass(frozen=True, slots=True)
class ASRExecutionRequest:
    video_id: UUID | str
    task_key: str
    provider: str
    model_id: str
    model_revision: str
    engine_version: str
    source_fingerprint: str
    media_ref: str
    estimated_api_cost: Decimal | float | int | None
    estimated_asr_cost: Decimal | float | int | None
    cost_currency: str
    execute: bool = False
    confirmation: str = ""
    max_polls: int = 0
    budget_date: date | None = None


@dataclass(frozen=True, slots=True)
class ASRProviderRequest:
    task_key: str
    media_ref: str
    source_fingerprint: str
    model_id: str
    model_revision: str
    engine_version: str


@dataclass(frozen=True, slots=True)
class ASRProviderState:
    status: str
    provider_task_ref: str
    evidence: TranscriptEvidence | None = None
    cost: TaskCost | None = None
    error_code: str | None = None


class ASRProvider(Protocol):
    provider_name: str
    max_retries: int

    def submit(self, request: ASRProviderRequest) -> ASRProviderState: ...

    def poll(self, provider_task_ref: str) -> ASRProviderState: ...


class ASRExecutionCoordinator:
    """Submit once, persist provider state, then poll without automatic retry."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def run(
        self,
        request: ASRExecutionRequest,
        *,
        provider_factory: Callable[[], ASRProvider],
    ) -> dict[str, object]:
        _validate_request(request)
        preview = _preview(request)
        if not request.execute:
            return preview
        if request.confirmation != ASR_CONFIRMATION:
            raise PermissionError("exact paid-operation confirmation is required")
        if request.estimated_api_cost is None or request.estimated_asr_cost is None:
            raise ValueError("known API and ASR estimates are required for paid execution")

        with self._single_paid_job():
            existing = self._load_job(request.task_key)
            if existing is not None:
                self._assert_job_matches(existing, request)
                if existing["status"] in {"completed", "failed"}:
                    return self._redacted(existing, external_calls=0, created=False)
                if existing["status"] == "submitting":
                    return {
                        "status": "reconciliation_required",
                        "execute": True,
                        "created": False,
                        "external_calls": 0,
                        "sdk_retries": 0,
                        "llm_calls": 0,
                    }
                if request.max_polls == 0:
                    return self._redacted(existing, external_calls=0, created=False)
                provider = provider_factory()
                self._assert_provider(provider, request)
                return self._poll_existing(request, existing, provider)

            self._preflight(request)
            provider = provider_factory()
            self._assert_provider(provider, request)
            job = self._reserve_and_create(request)
            external_calls = 0
            try:
                external_calls += 1
                state = provider.submit(_provider_request(request))
                job = self._apply_state(request, job["id"], state)
            except Exception:
                self._mark_submit_failed(request, job["id"])
                failed = self._load_job(request.task_key)
                assert failed is not None
                return self._redacted(
                    failed,
                    external_calls=external_calls,
                    created=True,
                )

            if state.status in {"submitted", "running"} and request.max_polls:
                result = self._poll_existing(
                    request,
                    job,
                    provider,
                    initial_external_calls=external_calls,
                    created=True,
                )
                return result
            return self._redacted(
                job,
                external_calls=external_calls,
                created=True,
            )

    def _poll_existing(
        self,
        request: ASRExecutionRequest,
        job: dict[str, object],
        provider: ASRProvider,
        *,
        initial_external_calls: int = 0,
        created: bool = False,
    ) -> dict[str, object]:
        external_calls = initial_external_calls
        provider_task_ref = str(job["provider_task_ref"])
        for _ in range(request.max_polls):
            self._reserve_poll(request, UUID(str(job["id"])))
            try:
                external_calls += 1
                state = provider.poll(provider_task_ref)
                if state.provider_task_ref != provider_task_ref:
                    raise ValueError("ASR provider task reference changed during polling")
                job = self._apply_state(request, UUID(str(job["id"])), state)
            except Exception:
                self._record_poll_error(UUID(str(job["id"])))
                break
            if state.status in {"completed", "failed"}:
                break
        fresh = self._load_job(request.task_key)
        assert fresh is not None
        return self._redacted(
            fresh,
            external_calls=external_calls,
            created=created,
        )

    def _preflight(self, request: ASRExecutionRequest) -> None:
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
                raise ValueError("paid ASR requires research level 2")
            cur.execute(
                """
                select max_requests, max_cost, cost_currency
                from daily_budget
                where budget_date=%s and provider=%s and budget_key=%s
                """,
                (budget_date, request.provider, ASR_BUDGET_KEY),
            )
            budget = cur.fetchone()
            if budget is None or (budget[0] is None and budget[1] is None):
                raise RuntimeError("daily ASR budget must be configured")
            if budget[2] != request.cost_currency:
                raise RuntimeError("daily ASR budget currency does not match request")

    def _reserve_and_create(self, request: ASRExecutionRequest) -> dict[str, object]:
        budget_date = request.budget_date or date.today()
        estimated_api = _decimal(request.estimated_api_cost)
        estimated_asr = _decimal(request.estimated_asr_cost)
        assert estimated_api is not None and estimated_asr is not None
        estimated_total = estimated_api + estimated_asr
        job_id = uuid4()
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            _reserve_budget(
                cur,
                budget_date=budget_date,
                provider=request.provider,
                currency=request.cost_currency,
                requests=1,
                estimated_cost=estimated_total,
            )
            cur.execute(
                """
                insert into asr_execution_job(
                  id, task_key, video_id, provider, model_id, model_revision,
                  engine_version, source_fingerprint, media_ref_fingerprint,
                  status, submission_count, poll_count,
                  estimated_api_cost, estimated_asr_cost, cost_currency,
                  budget_date, budget_key, metadata
                ) values (
                  %s,%s,%s,%s,%s,%s,%s,%s,%s,
                  'submitting',1,0,%s,%s,%s,%s,%s,%s
                )
                """,
                (
                    job_id,
                    request.task_key,
                    request.video_id,
                    request.provider,
                    request.model_id,
                    request.model_revision,
                    request.engine_version,
                    request.source_fingerprint,
                    _hash(request.media_ref),
                    estimated_api,
                    estimated_asr,
                    request.cost_currency,
                    budget_date,
                    ASR_BUDGET_KEY,
                    Jsonb(
                        {
                            "manual_only": True,
                            "sdk_retries": 0,
                            "media_ref_stored": False,
                            "semantic_inference": False,
                        }
                    ),
                ),
            )
            conn.commit()
        job = self._load_job(request.task_key)
        assert job is not None
        return job

    def _reserve_poll(self, request: ASRExecutionRequest, job_id: UUID) -> None:
        budget_date = request.budget_date or date.today()
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            _reserve_budget(
                cur,
                budget_date=budget_date,
                provider=request.provider,
                currency=request.cost_currency,
                requests=1,
                estimated_cost=Decimal("0"),
            )
            cur.execute(
                """
                update asr_execution_job
                set poll_count=poll_count+1, updated_at=now()
                where id=%s
                """,
                (job_id,),
            )
            conn.commit()

    def _apply_state(
        self,
        request: ASRExecutionRequest,
        job_id: UUID,
        state: ASRProviderState,
    ) -> dict[str, object]:
        _validate_provider_state(state)
        if state.cost is not None and state.cost.currency != request.cost_currency:
            raise ValueError("ASR provider cost currency does not match request")
        if state.status == "completed":
            assert state.evidence is not None and state.cost is not None
            _assert_evidence_matches_request(request, state.evidence)
            record = TranscriptEvidenceStore(self.dsn).ingest(
                request.video_id,
                task_key=request.task_key,
                evidence=state.evidence,
                cost=state.cost,
            )
            task_cost_id = record.task_cost_id
        elif state.status == "failed":
            task_cost_id = self._record_failed_cost(request, state)
        else:
            task_cost_id = None

        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                update asr_execution_job
                set status=%s,
                    provider_task_ref=%s,
                    task_cost_id=coalesce(%s,task_cost_id),
                    error_code=%s,
                    updated_at=now()
                where id=%s
                """,
                (
                    state.status,
                    state.provider_task_ref,
                    task_cost_id,
                    _safe_error_code(state.error_code),
                    job_id,
                ),
            )
            conn.commit()
        job = self._load_job(request.task_key)
        assert job is not None
        return job

    def _record_failed_cost(
        self,
        request: ASRExecutionRequest,
        state: ASRProviderState,
    ) -> UUID:
        cost = state.cost or TaskCost(
            api_cost=None,
            asr_cost=None,
            llm_cost=Decimal("0"),
            currency=request.cost_currency,
            basis="unknown",
        )
        api_cost, asr_cost, llm_cost = _validated_cost(cost)
        if cost.currency != request.cost_currency:
            raise ValueError("failed ASR cost currency does not match request")
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into research_task_cost(
                  task_key, task_type, task_version, video_id, status,
                  input_fingerprint, output_fingerprint,
                  api_cost, asr_cost, llm_cost,
                  cost_currency, cost_basis, metadata
                ) values (
                  %s,'asr_transcription',%s,%s,'failed',%s,null,
                  %s,%s,%s,%s,%s,%s
                )
                on conflict(task_key) do nothing
                returning id
                """,
                (
                    request.task_key,
                    ASR_EVIDENCE_VERSION,
                    request.video_id,
                    request.source_fingerprint,
                    api_cost,
                    asr_cost,
                    llm_cost,
                    cost.currency,
                    cost.basis,
                    Jsonb(
                        {
                            "external_execution": True,
                            "error_code": _safe_error_code(state.error_code),
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

    def _load_job(self, task_key: str) -> dict[str, object] | None:
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                select
                  id, task_key, video_id, provider, model_id, model_revision,
                  engine_version, source_fingerprint, media_ref_fingerprint,
                  status, provider_task_ref, submission_count, poll_count,
                  estimated_api_cost, estimated_asr_cost, cost_currency,
                  budget_date, budget_key, task_cost_id, error_code
                from asr_execution_job
                where task_key=%s
                """,
                (task_key,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        keys = (
            "id", "task_key", "video_id", "provider", "model_id",
            "model_revision", "engine_version", "source_fingerprint",
            "media_ref_fingerprint", "status", "provider_task_ref",
            "submission_count", "poll_count", "estimated_api_cost",
            "estimated_asr_cost", "cost_currency", "budget_date",
            "budget_key", "task_cost_id", "error_code",
        )
        return dict(zip(keys, row, strict=True))

    @staticmethod
    def _assert_job_matches(
        job: dict[str, object],
        request: ASRExecutionRequest,
    ) -> None:
        expected = (
            UUID(str(request.video_id)),
            request.provider,
            request.model_id,
            request.model_revision,
            request.engine_version,
            request.source_fingerprint,
            _hash(request.media_ref),
            _decimal(request.estimated_api_cost),
            _decimal(request.estimated_asr_cost),
            request.cost_currency,
            request.budget_date or date.today(),
            ASR_BUDGET_KEY,
        )
        actual = (
            job["video_id"],
            job["provider"],
            job["model_id"],
            job["model_revision"],
            job["engine_version"],
            job["source_fingerprint"],
            job["media_ref_fingerprint"],
            job["estimated_api_cost"],
            job["estimated_asr_cost"],
            job["cost_currency"],
            job["budget_date"],
            job["budget_key"],
        )
        if actual != expected:
            raise ValueError("task_key already exists with different ASR execution inputs")

    @staticmethod
    def _assert_provider(provider: ASRProvider, request: ASRExecutionRequest) -> None:
        if provider.provider_name != request.provider:
            raise ValueError("ASR provider does not match request")
        if provider.max_retries != 0:
            raise ValueError("ASR provider retries must be disabled")

    @contextmanager
    def _single_paid_job(self) -> Iterator[None]:
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("select pg_try_advisory_lock(hashtext(%s))", (ASR_LOCK_NAME,))
            if not cur.fetchone()[0]:
                raise RuntimeError("another manual ASR execution is already running")
            try:
                yield
            finally:
                cur.execute("select pg_advisory_unlock(hashtext(%s))", (ASR_LOCK_NAME,))

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
            "submission_count": int(job["submission_count"]),
            "poll_count": int(job["poll_count"]),
            "external_calls": external_calls,
            "has_cost_record": job["task_cost_id"] is not None,
            "error_code": job["error_code"],
            "sdk_retries": 0,
            "llm_calls": 0,
        }

    def _mark_submit_failed(
        self,
        request: ASRExecutionRequest,
        job_id: UUID,
    ) -> None:
        failed = ASRProviderState(
            status="failed",
            provider_task_ref="unavailable",
            cost=TaskCost(
                api_cost=None,
                asr_cost=None,
                llm_cost=Decimal("0"),
                currency=request.cost_currency,
                basis="unknown",
            ),
            error_code="submit_failed",
        )
        task_cost_id = self._record_failed_cost(request, failed)
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                update asr_execution_job
                set status='failed', error_code='submit_failed',
                    task_cost_id=%s, updated_at=now()
                where id=%s
                """,
                (task_cost_id, job_id),
            )
            conn.commit()

    def _record_poll_error(self, job_id: UUID) -> None:
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                update asr_execution_job
                set error_code='poll_failed', updated_at=now()
                where id=%s
                """,
                (job_id,),
            )
            conn.commit()


def _preview(request: ASRExecutionRequest) -> dict[str, object]:
    api = _decimal(request.estimated_api_cost)
    asr = _decimal(request.estimated_asr_cost)
    total = api + asr if api is not None and asr is not None else None
    return {
        "status": "preview",
        "execute": False,
        "maximum_external_calls": 1 + request.max_polls,
        "estimated_total_cost": float(total) if total is not None else None,
        "cost_currency": request.cost_currency,
        "execution_ready": total is not None,
        "sdk_retries": 0,
        "llm_calls": 0,
    }


def _validate_request(request: ASRExecutionRequest) -> None:
    required = {
        "task_key": request.task_key,
        "provider": request.provider,
        "model_id": request.model_id,
        "model_revision": request.model_revision,
        "engine_version": request.engine_version,
        "source_fingerprint": request.source_fingerprint,
        "media_ref": request.media_ref,
        "cost_currency": request.cost_currency,
    }
    missing = [name for name, value in required.items() if not str(value).strip()]
    if missing:
        raise ValueError(f"required ASR execution fields are missing: {missing}")
    if not 0 <= request.max_polls <= MAX_POLLS_PER_RUN:
        raise ValueError(f"max_polls must be between 0 and {MAX_POLLS_PER_RUN}")
    for value in (request.estimated_api_cost, request.estimated_asr_cost):
        parsed = _decimal(value)
        if parsed is not None and parsed < 0:
            raise ValueError("estimated costs cannot be negative")


def _provider_request(request: ASRExecutionRequest) -> ASRProviderRequest:
    return ASRProviderRequest(
        task_key=request.task_key,
        media_ref=request.media_ref,
        source_fingerprint=request.source_fingerprint,
        model_id=request.model_id,
        model_revision=request.model_revision,
        engine_version=request.engine_version,
    )


def _validate_provider_state(state: ASRProviderState) -> None:
    if state.status not in ALLOWED_PROVIDER_STATUSES:
        raise ValueError("invalid ASR provider status")
    if not state.provider_task_ref.strip():
        raise ValueError("provider task reference is required")
    if state.status == "completed" and (state.evidence is None or state.cost is None):
        raise ValueError("completed ASR state requires evidence and cost")
    if state.status != "completed" and state.evidence is not None:
        raise ValueError("non-completed ASR state cannot include evidence")
    if state.cost is not None:
        _validated_cost(state.cost)


def _assert_evidence_matches_request(
    request: ASRExecutionRequest,
    evidence: TranscriptEvidence,
) -> None:
    actual = (
        evidence.asr_provider,
        evidence.model_id,
        evidence.model_revision,
        evidence.engine_version,
        evidence.source_fingerprint,
    )
    expected = (
        request.provider,
        request.model_id,
        request.model_revision,
        request.engine_version,
        request.source_fingerprint,
    )
    if actual != expected:
        raise ValueError("ASR evidence version or input does not match execution request")


def _validated_cost(
    cost: TaskCost,
) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    values = (
        _decimal(cost.api_cost),
        _decimal(cost.asr_cost),
        _decimal(cost.llm_cost),
    )
    if any(item is not None and item < 0 for item in values):
        raise ValueError("task costs cannot be negative")
    if values[2] != Decimal("0"):
        raise ValueError("ASR execution requires explicit llm_cost=0")
    if not cost.currency.strip():
        raise ValueError("cost currency is required")
    if cost.basis not in {"actual", "estimated", "mixed", "unknown"}:
        raise ValueError("invalid cost basis")
    return values


def _reserve_budget(
    cur,
    *,
    budget_date: date,
    provider: str,
    currency: str,
    requests: int,
    estimated_cost: Decimal,
) -> None:
    cur.execute(
        """
        select max_cost, max_requests, spent_cost, used_requests, cost_currency
        from daily_budget
        where budget_date=%s and provider=%s and budget_key=%s
        for update
        """,
        (budget_date, provider, ASR_BUDGET_KEY),
    )
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("daily ASR budget must be configured")
    max_cost, max_requests, spent_cost, used_requests, configured_currency = row
    if configured_currency != currency:
        raise RuntimeError("daily ASR budget currency does not match request")
    next_requests = used_requests + requests
    next_cost = Decimal(spent_cost) + estimated_cost
    if max_requests is not None and next_requests > max_requests:
        raise RuntimeError("daily ASR request budget exceeded")
    if max_cost is not None and next_cost > Decimal(max_cost):
        raise RuntimeError("daily ASR cost budget exceeded")
    cur.execute(
        """
        update daily_budget
        set used_requests=%s, spent_cost=%s, updated_at=now()
        where budget_date=%s and provider=%s and budget_key=%s
        """,
        (next_requests, next_cost, budget_date, provider, ASR_BUDGET_KEY),
    )


def _safe_error_code(value: str | None) -> str | None:
    if value is None:
        return None
    safe = "".join(char for char in value if char.isalnum() or char in {"_", "-"})
    return safe[:64] or "provider_failed"


def _decimal(value: Decimal | float | int | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
