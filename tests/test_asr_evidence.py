from __future__ import annotations

import os
from decimal import Decimal
from uuid import UUID

import psycopg
import pytest

from douyin_research.l2 import (
    ASR_EVIDENCE_VERSION,
    TaskCost,
    TranscriptEvidence,
    TranscriptEvidenceStore,
    TranscriptSegment,
)


DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not configured")


def _clear() -> None:
    assert DSN
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("delete from source_video")
        cur.execute("delete from research_task_cost")
        conn.commit()


def _video(level: int = 2, key: str = "main") -> UUID:
    assert DSN
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into source_video(
              platform, platform_video_id, research_level
            ) values ('douyin', %s, %s)
            returning id
            """,
            (f"synthetic-asr-{key}", level),
        )
        video_id = cur.fetchone()[0]
        conn.commit()
    return video_id


def _evidence(
    *,
    source_fingerprint: str = "source-fingerprint-a",
    text: str = "示例文本",
    quality_status: str = "usable",
    segments: tuple[TranscriptSegment, ...] | None = None,
) -> TranscriptEvidence:
    return TranscriptEvidence(
        asr_provider="synthetic-asr",
        model_id="synthetic-model",
        model_revision="revision-1",
        engine_version="engine-1",
        language="zh-CN",
        text=text,
        segments=segments
        if segments is not None
        else (
            TranscriptSegment(0, 1000, "示例", 0.9),
            TranscriptSegment(1000, 2000, "文本", 0.8),
        ),
        hotword_version="none",
        source_provider="synthetic-source",
        source_fingerprint=source_fingerprint,
        audio_duration_ms=2000,
        quality_status=quality_status,
    )


def test_asr_evidence_is_versioned_costed_and_replay_safe() -> None:
    assert DSN
    _clear()
    video_id = _video()
    store = TranscriptEvidenceStore(DSN)
    evidence = _evidence()
    cost = TaskCost(
        api_cost=Decimal("0"),
        asr_cost=Decimal("0.125"),
        llm_cost=Decimal("0"),
        currency="CNY",
        basis="actual",
    )

    first = store.ingest(
        video_id,
        task_key="synthetic-asr-task-1",
        evidence=evidence,
        cost=cost,
    )
    replay = store.ingest(
        video_id,
        task_key="synthetic-asr-task-1",
        evidence=evidence,
        cost=cost,
    )

    assert first.created is True
    assert replay.created is False
    assert replay.transcript_id == first.transcript_id
    assert replay.task_cost_id == first.task_cost_id
    assert first.api_cost == Decimal("0")
    assert first.asr_cost == Decimal("0.125")
    assert first.llm_cost == Decimal("0")
    assert first.total_cost == Decimal("0.125")
    assert first.quality_status == "usable"

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select
              count(*), min(task_version), min(total_cost),
              min(metadata->>'external_call_performed_by_store')
            from research_task_cost
            where video_id=%s
            """,
            (video_id,),
        )
        assert cur.fetchone() == (1, ASR_EVIDENCE_VERSION, Decimal("0.125"), "false")
        cur.execute(
            """
            select count(*), min(cost_amount), min(metadata->>'segment_count')
            from transcript
            where video_id=%s
            """,
            (video_id,),
        )
        assert cur.fetchone() == (1, Decimal("0.125"), "2")
        cur.execute(
            "select research_level from source_video where id=%s",
            (video_id,),
        )
        assert cur.fetchone()[0] == 2


def test_unknown_costs_stay_null_and_explicit_zero_stays_zero() -> None:
    assert DSN
    _clear()
    video_id = _video(key="unknown-cost")
    result = TranscriptEvidenceStore(DSN).ingest(
        video_id,
        task_key="synthetic-asr-task-unknown-cost",
        evidence=_evidence(
            source_fingerprint="source-fingerprint-no-speech",
            text="",
            quality_status="no_speech",
            segments=(),
        ),
        cost=TaskCost(
            api_cost=None,
            asr_cost=None,
            llm_cost=Decimal("0"),
            currency="CNY",
            basis="unknown",
        ),
    )

    assert result.api_cost is None
    assert result.asr_cost is None
    assert result.llm_cost == Decimal("0")
    assert result.total_cost is None
    assert result.quality_status == "no_speech"

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select api_cost, asr_cost, llm_cost, total_cost
            from research_task_cost
            where id=%s
            """,
            (result.task_cost_id,),
        )
        assert cur.fetchone() == (None, None, Decimal("0"), None)


def test_asr_evidence_requires_l2_and_never_accepts_llm_cost() -> None:
    assert DSN
    _clear()
    video_id = _video(level=1)
    store = TranscriptEvidenceStore(DSN)

    with pytest.raises(ValueError, match="research level 2"):
        store.ingest(
            video_id,
            task_key="synthetic-asr-task-level-1",
            evidence=_evidence(),
            cost=TaskCost(api_cost=0, asr_cost=0, llm_cost=0),
        )

    level_two_id = _video(level=2, key="llm-cost")
    with pytest.raises(ValueError, match="llm_cost=0"):
        store.ingest(
            level_two_id,
            task_key="synthetic-asr-task-with-llm",
            evidence=_evidence(source_fingerprint="source-fingerprint-llm"),
            cost=TaskCost(api_cost=0, asr_cost=0, llm_cost=1),
        )


@pytest.mark.parametrize(
    "segments, message",
    [
        ((TranscriptSegment(10, 10, "bad"),), "timestamps"),
        (
            (
                TranscriptSegment(0, 1000, "first"),
                TranscriptSegment(900, 1200, "overlap"),
            ),
            "non-overlapping",
        ),
        ((TranscriptSegment(0, 2100, "too long"),), "exceeds"),
        ((TranscriptSegment(0, 1000, "bad confidence", 1.1),), "confidence"),
    ],
)
def test_asr_segment_boundaries_are_validated(segments, message: str) -> None:
    assert DSN
    _clear()
    video_id = _video(key=message)
    with pytest.raises(ValueError, match=message):
        TranscriptEvidenceStore(DSN).ingest(
            video_id,
            task_key=f"synthetic-asr-invalid-{message}",
            evidence=_evidence(
                source_fingerprint=f"source-fingerprint-{message}",
                segments=segments,
            ),
            cost=TaskCost(api_cost=0, asr_cost=0, llm_cost=0),
        )


def test_task_key_and_evidence_identity_are_immutable() -> None:
    assert DSN
    _clear()
    video_id = _video(key="immutable")
    store = TranscriptEvidenceStore(DSN)
    original = _evidence(source_fingerprint="source-fingerprint-immutable")
    cost = TaskCost(api_cost=0, asr_cost=0, llm_cost=0, basis="actual")
    store.ingest(
        video_id,
        task_key="synthetic-asr-immutable",
        evidence=original,
        cost=cost,
    )

    changed = _evidence(
        source_fingerprint="source-fingerprint-immutable",
        text="修改后的文本",
    )
    with pytest.raises(ValueError, match="task_key already exists"):
        store.ingest(
            video_id,
            task_key="synthetic-asr-immutable",
            evidence=changed,
            cost=cost,
        )

    with pytest.raises(ValueError, match="another task_key"):
        store.ingest(
            video_id,
            task_key="synthetic-asr-duplicate-evidence",
            evidence=original,
            cost=cost,
        )
