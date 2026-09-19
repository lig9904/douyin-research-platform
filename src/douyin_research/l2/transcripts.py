"""Versioned ASR evidence ingestion with nullable per-task cost accounting."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb


ASR_EVIDENCE_VERSION = "asr-evidence-v1.0.0"
QUALITY_STATUSES = frozenset(
    {"unreviewed", "usable", "low_confidence", "no_speech", "rejected"}
)
COST_BASES = frozenset({"actual", "estimated", "mixed", "unknown"})


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    start_ms: int
    end_ms: int
    text: str
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class TranscriptEvidence:
    asr_provider: str
    model_id: str
    model_revision: str
    engine_version: str
    source_fingerprint: str
    text: str
    segments: tuple[TranscriptSegment, ...] = ()
    language: str | None = None
    hotword_version: str | None = None
    source_provider: str | None = None
    audio_duration_ms: int | None = None
    quality_status: str = "unreviewed"


@dataclass(frozen=True, slots=True)
class TaskCost:
    api_cost: Decimal | float | int | None
    asr_cost: Decimal | float | int | None
    llm_cost: Decimal | float | int | None = Decimal("0")
    currency: str = "CNY"
    basis: str = "unknown"


@dataclass(frozen=True, slots=True)
class TranscriptRecord:
    transcript_id: UUID
    task_cost_id: UUID
    created: bool
    quality_status: str
    api_cost: Decimal | None
    asr_cost: Decimal | None
    llm_cost: Decimal | None
    total_cost: Decimal | None
    cost_currency: str


class TranscriptEvidenceStore:
    """Persist already-produced ASR evidence; never invokes an ASR provider."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def ingest(
        self,
        video_id: UUID | str,
        *,
        task_key: str,
        evidence: TranscriptEvidence,
        cost: TaskCost,
        pipeline_run_id: UUID | str | None = None,
    ) -> TranscriptRecord:
        _validate_task_key(task_key)
        _validate_evidence(evidence)
        costs = _validate_cost(cost)
        output_fingerprint = _output_fingerprint(evidence)

        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "select research_level from source_video where id=%s",
                (video_id,),
            )
            video = cur.fetchone()
            if video is None:
                raise ValueError("source video does not exist")
            if video[0] < 2:
                raise ValueError("ASR evidence requires research level 2")

            cur.execute(
                """
                select
                  id, video_id, pipeline_run_id, input_fingerprint,
                  output_fingerprint, api_cost, asr_cost, llm_cost,
                  total_cost, cost_currency, cost_basis
                from research_task_cost
                where task_key=%s
                """,
                (task_key,),
            )
            existing = cur.fetchone()
            if existing is not None:
                self._assert_replay_matches(
                    existing,
                    video_id=video_id,
                    pipeline_run_id=pipeline_run_id,
                    evidence=evidence,
                    output_fingerprint=output_fingerprint,
                    costs=costs,
                    cost=cost,
                )
                task_cost_id = existing[0]
                cur.execute(
                    """
                    select id, quality_status
                    from transcript
                    where task_cost_id=%s
                    """,
                    (task_cost_id,),
                )
                transcript = cur.fetchone()
                if transcript is None:
                    raise RuntimeError("ASR task exists without transcript evidence")
                return TranscriptRecord(
                    transcript_id=transcript[0],
                    task_cost_id=task_cost_id,
                    created=False,
                    quality_status=transcript[1],
                    api_cost=existing[5],
                    asr_cost=existing[6],
                    llm_cost=existing[7],
                    total_cost=existing[8],
                    cost_currency=existing[9],
                )

            self._assert_evidence_not_registered(cur, video_id, evidence)
            api_cost, asr_cost, llm_cost = costs
            cur.execute(
                """
                insert into research_task_cost(
                  task_key, task_type, task_version, pipeline_run_id, video_id,
                  status, input_fingerprint, output_fingerprint,
                  api_cost, asr_cost, llm_cost, cost_currency, cost_basis,
                  metadata
                ) values (
                  %s,'asr_transcription',%s,%s,%s,'completed',%s,%s,
                  %s,%s,%s,%s,%s,%s
                )
                returning id, total_cost
                """,
                (
                    task_key,
                    ASR_EVIDENCE_VERSION,
                    pipeline_run_id,
                    video_id,
                    evidence.source_fingerprint,
                    output_fingerprint,
                    api_cost,
                    asr_cost,
                    llm_cost,
                    cost.currency,
                    cost.basis,
                    Jsonb(
                        {
                            "external_call_performed_by_store": False,
                            "measurement_kind": "asr",
                            "semantic_inference": False,
                        }
                    ),
                ),
            )
            task_cost_id, total_cost = cur.fetchone()
            segment_payload = [
                {
                    "start_ms": item.start_ms,
                    "end_ms": item.end_ms,
                    "text": item.text,
                    "confidence": item.confidence,
                }
                for item in evidence.segments
            ]
            cur.execute(
                """
                insert into transcript(
                  video_id, asr_provider, model_id, model_revision,
                  engine_version, language, text_content, segments,
                  hotword_version, source_provider, source_fingerprint,
                  audio_duration_ms, quality_status, cost_amount,
                  cost_currency, metadata, task_cost_id
                ) values (
                  %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                )
                returning id
                """,
                (
                    video_id,
                    evidence.asr_provider,
                    evidence.model_id,
                    evidence.model_revision,
                    evidence.engine_version,
                    evidence.language,
                    evidence.text,
                    Jsonb(segment_payload),
                    evidence.hotword_version,
                    evidence.source_provider,
                    evidence.source_fingerprint,
                    evidence.audio_duration_ms,
                    evidence.quality_status,
                    asr_cost,
                    cost.currency,
                    Jsonb(
                        {
                            "evidence_version": ASR_EVIDENCE_VERSION,
                            "segment_count": len(evidence.segments),
                            "measurement_error_possible": True,
                            "semantic_inference": False,
                        }
                    ),
                    task_cost_id,
                ),
            )
            transcript_id = cur.fetchone()[0]
            conn.commit()

        return TranscriptRecord(
            transcript_id=transcript_id,
            task_cost_id=task_cost_id,
            created=True,
            quality_status=evidence.quality_status,
            api_cost=api_cost,
            asr_cost=asr_cost,
            llm_cost=llm_cost,
            total_cost=total_cost,
            cost_currency=cost.currency,
        )

    @staticmethod
    def _assert_replay_matches(
        row,
        *,
        video_id: UUID | str,
        pipeline_run_id: UUID | str | None,
        evidence: TranscriptEvidence,
        output_fingerprint: str,
        costs: tuple[Decimal | None, Decimal | None, Decimal | None],
        cost: TaskCost,
    ) -> None:
        expected_video_id = UUID(str(video_id))
        expected_run_id = UUID(str(pipeline_run_id)) if pipeline_run_id else None
        expected = (
            expected_video_id,
            expected_run_id,
            evidence.source_fingerprint,
            output_fingerprint,
            *costs,
            cost.currency,
            cost.basis,
        )
        actual = (
            row[1],
            row[2],
            row[3],
            row[4],
            row[5],
            row[6],
            row[7],
            row[9],
            row[10],
        )
        if actual != expected:
            raise ValueError("task_key already exists with different ASR evidence or cost")

    @staticmethod
    def _assert_evidence_not_registered(cur, video_id, evidence: TranscriptEvidence) -> None:
        cur.execute(
            """
            select id
            from transcript
            where video_id=%s
              and asr_provider=%s
              and coalesce(model_id,'')=%s
              and coalesce(model_revision,'')=%s
              and coalesce(engine_version,'')=%s
              and coalesce(language,'')=%s
              and coalesce(hotword_version,'')=%s
              and coalesce(source_provider,'')=%s
              and coalesce(source_fingerprint,'')=%s
            """,
            (
                video_id,
                evidence.asr_provider,
                evidence.model_id,
                evidence.model_revision,
                evidence.engine_version,
                evidence.language or "",
                evidence.hotword_version or "",
                evidence.source_provider or "",
                evidence.source_fingerprint,
            ),
        )
        if cur.fetchone() is not None:
            raise ValueError("ASR evidence already exists under another task_key")


def _validate_task_key(task_key: str) -> None:
    if not task_key.strip():
        raise ValueError("task_key is required")
    if len(task_key) > 200:
        raise ValueError("task_key is too long")


def _validate_evidence(evidence: TranscriptEvidence) -> None:
    required = {
        "asr_provider": evidence.asr_provider,
        "model_id": evidence.model_id,
        "model_revision": evidence.model_revision,
        "engine_version": evidence.engine_version,
        "source_fingerprint": evidence.source_fingerprint,
    }
    missing = [name for name, value in required.items() if not value.strip()]
    if missing:
        raise ValueError(f"required ASR version fields are missing: {missing}")
    if evidence.quality_status not in QUALITY_STATUSES:
        raise ValueError("invalid ASR quality_status")
    if evidence.quality_status != "no_speech" and not evidence.text.strip():
        raise ValueError("ASR text is required unless quality_status is no_speech")
    if evidence.audio_duration_ms is not None and evidence.audio_duration_ms < 0:
        raise ValueError("audio_duration_ms cannot be negative")

    previous_end = 0
    for item in evidence.segments:
        if item.start_ms < 0 or item.end_ms <= item.start_ms:
            raise ValueError("ASR segment timestamps are invalid")
        if item.start_ms < previous_end:
            raise ValueError("ASR segments must be ordered and non-overlapping")
        if evidence.audio_duration_ms is not None and item.end_ms > evidence.audio_duration_ms:
            raise ValueError("ASR segment exceeds audio duration")
        if not item.text.strip():
            raise ValueError("ASR segment text cannot be empty")
        if item.confidence is not None and not 0 <= item.confidence <= 1:
            raise ValueError("ASR segment confidence must be between 0 and 1")
        previous_end = item.end_ms


def _validate_cost(
    cost: TaskCost,
) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    if not cost.currency.strip():
        raise ValueError("cost currency is required")
    if cost.basis not in COST_BASES:
        raise ValueError("invalid cost basis")
    values = tuple(_decimal(value) for value in (cost.api_cost, cost.asr_cost, cost.llm_cost))
    if any(value is not None and value < 0 for value in values):
        raise ValueError("task costs cannot be negative")
    if values[2] != Decimal("0"):
        raise ValueError("ASR evidence ingestion requires explicit llm_cost=0")
    return values


def _decimal(value: Decimal | float | int | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _output_fingerprint(evidence: TranscriptEvidence) -> str:
    payload: dict[str, Any] = {
        "evidence_version": ASR_EVIDENCE_VERSION,
        "asr_provider": evidence.asr_provider,
        "model_id": evidence.model_id,
        "model_revision": evidence.model_revision,
        "engine_version": evidence.engine_version,
        "language": evidence.language,
        "text": evidence.text,
        "segments": [
            {
                "start_ms": item.start_ms,
                "end_ms": item.end_ms,
                "text": item.text,
                "confidence": item.confidence,
            }
            for item in evidence.segments
        ],
        "hotword_version": evidence.hotword_version,
        "source_provider": evidence.source_provider,
        "source_fingerprint": evidence.source_fingerprint,
        "audio_duration_ms": evidence.audio_duration_ms,
        "quality_status": evidence.quality_status,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
