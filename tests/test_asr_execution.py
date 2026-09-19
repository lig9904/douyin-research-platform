from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from uuid import UUID

import psycopg
import pytest

from douyin_research.l0l1 import DailyBudgetGuard
from douyin_research.l2 import (
    ASR_BUDGET_KEY,
    ASR_CONFIRMATION,
    ASRExecutionCoordinator,
    ASRExecutionRequest,
    ASRProviderState,
    TaskCost,
    TranscriptEvidence,
    TranscriptSegment,
)


DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not configured")
BUDGET_DATE = date(2026, 9, 19)
PROVIDER = "synthetic-asr"


def _clear() -> None:
    assert DSN
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("delete from source_video")
        cur.execute("delete from research_task_cost")
        cur.execute("delete from daily_budget")
        conn.commit()


def _video(level: int = 2, key: str = "main") -> UUID:
    assert DSN
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into source_video(platform, platform_video_id, research_level)
            values ('douyin', %s, %s)
            returning id
            """,
            (f"synthetic-asr-execution-{key}", level),
        )
        video_id = cur.fetchone()[0]
        conn.commit()
    return video_id


def _budget(
    *,
    max_requests: int = 10,
    max_cost: Decimal = Decimal("10"),
    currency: str = "CNY",
) -> None:
    assert DSN
    DailyBudgetGuard(DSN).configure(
        provider=PROVIDER,
        budget_key=ASR_BUDGET_KEY,
        max_cost=float(max_cost),
        max_requests=max_requests,
        budget_date=BUDGET_DATE,
        cost_currency=currency,
    )


def _request(video_id: UUID, **overrides) -> ASRExecutionRequest:
    values = {
        "video_id": video_id,
        "task_key": "synthetic-asr-execution-task",
        "provider": PROVIDER,
        "model_id": "synthetic-model",
        "model_revision": "revision-1",
        "engine_version": "engine-1",
        "source_fingerprint": "source-fingerprint",
        "media_ref": "private-media-reference",
        "estimated_api_cost": Decimal("0.1"),
        "estimated_asr_cost": Decimal("0.2"),
        "cost_currency": "CNY",
        "execute": False,
        "confirmation": "",
        "max_polls": 0,
        "budget_date": BUDGET_DATE,
    }
    values.update(overrides)
    return ASRExecutionRequest(**values)


def _evidence() -> TranscriptEvidence:
    return TranscriptEvidence(
        asr_provider=PROVIDER,
        model_id="synthetic-model",
        model_revision="revision-1",
        engine_version="engine-1",
        source_fingerprint="source-fingerprint",
        text="合成转写",
        segments=(TranscriptSegment(0, 1000, "合成转写", 0.9),),
        language="zh-CN",
        source_provider="synthetic-media",
        audio_duration_ms=1000,
        quality_status="usable",
    )


class FakeProvider:
    provider_name = PROVIDER
    max_retries = 0

    def __init__(self, submit_state, poll_states=()):
        self.submit_state = submit_state
        self.poll_states = list(poll_states)
        self.submit_calls = []
        self.poll_calls = []

    def submit(self, request):
        self.submit_calls.append(request)
        if isinstance(self.submit_state, Exception):
            raise self.submit_state
        return self.submit_state

    def poll(self, provider_task_ref):
        self.poll_calls.append(provider_task_ref)
        return self.poll_states.pop(0)


def test_preview_and_wrong_confirmation_never_load_provider() -> None:
    assert DSN
    _clear()
    video_id = _video()
    coordinator = ASRExecutionCoordinator(DSN)
    calls = []

    preview = coordinator.run(
        _request(video_id),
        provider_factory=lambda: calls.append("provider") or None,
    )

    assert calls == []
    assert preview == {
        "status": "preview",
        "execute": False,
        "maximum_external_calls": 1,
        "estimated_total_cost": 0.3,
        "cost_currency": "CNY",
        "execution_ready": True,
        "sdk_retries": 0,
        "llm_calls": 0,
    }
    assert "video_id" not in preview
    assert "task_key" not in preview
    assert "media_ref" not in preview

    with pytest.raises(PermissionError, match="exact paid-operation confirmation"):
        coordinator.run(
            _request(video_id, execute=True, confirmation="yes"),
            provider_factory=lambda: calls.append("provider") or None,
        )
    assert calls == []


def test_preflight_stops_before_provider_and_budget_is_required() -> None:
    assert DSN
    _clear()
    video_id = _video(level=1)
    coordinator = ASRExecutionCoordinator(DSN)
    calls = []

    with pytest.raises(ValueError, match="research level 2"):
        coordinator.run(
            _request(
                video_id,
                execute=True,
                confirmation=ASR_CONFIRMATION,
            ),
            provider_factory=lambda: calls.append("provider") or None,
        )
    assert calls == []

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "update source_video set research_level=2 where id=%s",
            (video_id,),
        )
        conn.commit()

    with pytest.raises(RuntimeError, match="budget must be configured"):
        coordinator.run(
            _request(
                video_id,
                execute=True,
                confirmation=ASR_CONFIRMATION,
            ),
            provider_factory=lambda: calls.append("provider") or None,
        )
    assert calls == []


def test_submit_poll_complete_is_bounded_costed_and_replay_safe() -> None:
    assert DSN
    _clear()
    video_id = _video()
    _budget()
    submitted = ASRProviderState(
        status="submitted",
        provider_task_ref="private-provider-task",
    )
    completed = ASRProviderState(
        status="completed",
        provider_task_ref="private-provider-task",
        evidence=_evidence(),
        cost=TaskCost(
            api_cost=Decimal("0.08"),
            asr_cost=Decimal("0.18"),
            llm_cost=Decimal("0"),
            currency="CNY",
            basis="actual",
        ),
    )
    provider = FakeProvider(submitted, [completed])
    request = _request(
        video_id,
        execute=True,
        confirmation=ASR_CONFIRMATION,
        max_polls=1,
    )
    coordinator = ASRExecutionCoordinator(DSN)

    result = coordinator.run(request, provider_factory=lambda: provider)
    replay_calls = []
    replay = coordinator.run(
        request,
        provider_factory=lambda: replay_calls.append("provider") or provider,
    )

    assert result["status"] == "completed"
    assert result["external_calls"] == 2
    assert result["submission_count"] == 1
    assert result["poll_count"] == 1
    assert result["has_cost_record"] is True
    assert replay["status"] == "completed"
    assert replay["external_calls"] == 0
    assert replay_calls == []
    assert len(provider.submit_calls) == 1
    assert provider.poll_calls == ["private-provider-task"]
    assert "private-provider-task" not in repr(result)
    assert "private-media-reference" not in repr(result)

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select used_requests, spent_cost
            from daily_budget
            where budget_date=%s and provider=%s and budget_key=%s
            """,
            (BUDGET_DATE, PROVIDER, ASR_BUDGET_KEY),
        )
        assert cur.fetchone() == (2, Decimal("0.3"))
        cur.execute(
            """
            select status, submission_count, poll_count,
                   media_ref_fingerprint, task_cost_id is not null
            from asr_execution_job
            where task_key=%s
            """,
            (request.task_key,),
        )
        job = cur.fetchone()
        assert job[:3] == ("completed", 1, 1)
        assert job[3] != request.media_ref
        assert job[4] is True
        cur.execute(
            """
            select api_cost, asr_cost, llm_cost, total_cost
            from research_task_cost
            where task_key=%s
            """,
            (request.task_key,),
        )
        assert cur.fetchone() == (
            Decimal("0.08"),
            Decimal("0.18"),
            Decimal("0"),
            Decimal("0.26"),
        )


def test_submitted_job_is_polled_without_resubmission() -> None:
    assert DSN
    _clear()
    video_id = _video(key="resume")
    _budget()
    submitted = ASRProviderState(
        status="submitted",
        provider_task_ref="private-provider-task",
    )
    first_provider = FakeProvider(submitted)
    coordinator = ASRExecutionCoordinator(DSN)

    first = coordinator.run(
        _request(
            video_id,
            execute=True,
            confirmation=ASR_CONFIRMATION,
            max_polls=0,
        ),
        provider_factory=lambda: first_provider,
    )
    assert first["status"] == "submitted"
    assert first["external_calls"] == 1

    running = ASRProviderState(
        status="running",
        provider_task_ref="private-provider-task",
    )
    second_provider = FakeProvider(submitted, [running])
    second = coordinator.run(
        _request(
            video_id,
            execute=True,
            confirmation=ASR_CONFIRMATION,
            max_polls=1,
        ),
        provider_factory=lambda: second_provider,
    )

    assert second["status"] == "running"
    assert second["external_calls"] == 1
    assert second_provider.submit_calls == []
    assert second_provider.poll_calls == ["private-provider-task"]


def test_submit_failure_is_not_retried_and_unknown_actual_cost_stays_null() -> None:
    assert DSN
    _clear()
    video_id = _video(key="failure")
    _budget()
    provider = FakeProvider(RuntimeError("secret request payload"))
    request = _request(
        video_id,
        execute=True,
        confirmation=ASR_CONFIRMATION,
    )
    coordinator = ASRExecutionCoordinator(DSN)

    result = coordinator.run(request, provider_factory=lambda: provider)
    replay_calls = []
    replay = coordinator.run(
        request,
        provider_factory=lambda: replay_calls.append("provider") or provider,
    )

    assert result["status"] == "failed"
    assert result["error_code"] == "submit_failed"
    assert result["external_calls"] == 1
    assert result["has_cost_record"] is True
    assert replay["external_calls"] == 0
    assert replay_calls == []
    assert len(provider.submit_calls) == 1
    assert "secret request payload" not in repr(result)

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
            None,
            None,
            Decimal("0"),
            None,
            "failed",
        )


def test_provider_retries_and_changed_task_inputs_are_rejected() -> None:
    assert DSN
    _clear()
    video_id = _video(key="provider-guard")
    _budget()
    submitted = ASRProviderState(
        status="submitted",
        provider_task_ref="private-provider-task",
    )
    retrying = FakeProvider(submitted)
    retrying.max_retries = 1
    coordinator = ASRExecutionCoordinator(DSN)
    request = _request(
        video_id,
        execute=True,
        confirmation=ASR_CONFIRMATION,
    )

    with pytest.raises(ValueError, match="retries must be disabled"):
        coordinator.run(request, provider_factory=lambda: retrying)

    valid = FakeProvider(submitted)
    coordinator.run(request, provider_factory=lambda: valid)
    with pytest.raises(ValueError, match="different ASR execution inputs"):
        coordinator.run(
            _request(
                video_id,
                execute=True,
                confirmation=ASR_CONFIRMATION,
                media_ref="different-private-media-reference",
            ),
            provider_factory=lambda: valid,
        )

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select used_requests
            from daily_budget
            where budget_date=%s and provider=%s and budget_key=%s
            """,
            (BUDGET_DATE, PROVIDER, ASR_BUDGET_KEY),
        )
        assert cur.fetchone()[0] == 1
