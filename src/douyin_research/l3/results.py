"""Versioned storage boundary for already-produced L3 research results."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from douyin_research.l2.transcripts import TaskCost


L3_ANALYSIS_TYPE = "l3_structured_research"
L3_SCHEMA_VERSION = "l3-research-v1.0.0"
EVIDENCE_MODALITIES = frozenset(
    {"metadata", "comments", "transcript", "visual", "ocr", "comparison"}
)
MAX_SECTION_ITEMS = 12
MAX_ITEM_CHARS = 500
COST_BASES = frozenset({"actual", "estimated", "mixed", "unknown"})


@dataclass(frozen=True, slots=True)
class L3ResearchResult:
    model_id: str
    model_revision: str
    prompt_version: str
    schema_version: str
    input_fingerprint: str
    evidence_modalities: tuple[str, ...]
    narrative_structure: tuple[str, ...]
    hook_functions: tuple[str, ...]
    comment_semantics: tuple[str, ...]
    case_comparisons: tuple[str, ...]
    mechanism_hypotheses: tuple[str, ...]
    ip_fit: tuple[str, ...]
    limitations: tuple[str, ...]
    privacy_reviewed: bool = False


@dataclass(frozen=True, slots=True)
class L3ResearchRecord:
    analysis_run_id: UUID
    task_cost_id: UUID
    created: bool
    research_level: int
    api_cost: Decimal | None
    asr_cost: Decimal | None
    llm_cost: Decimal | None
    total_cost: Decimal | None
    cost_currency: str


class L3ResearchStore:
    """Persist completed L3 output; never invokes an API, ASR engine, or LLM."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def ingest(
        self,
        video_id: UUID | str,
        *,
        task_key: str,
        result: L3ResearchResult,
        cost: TaskCost,
        pipeline_run_id: UUID | str | None = None,
    ) -> L3ResearchRecord:
        _validate_task_key(task_key)
        _validate_result(result)
        costs = _validate_cost(cost)
        output_fingerprint = _output_fingerprint(result)

        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "select research_level from source_video where id=%s",
                (video_id,),
            )
            video = cur.fetchone()
            if video is None:
                raise ValueError("source video does not exist")
            if video[0] < 2:
                raise ValueError("L3 analysis requires research level 2")

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
                (video_id,),
            )
            if not cur.fetchone()[0]:
                raise ValueError("video was not selected by the L2 to L3 gate")

            cur.execute(
                """
                select
                  c.id, c.video_id, c.pipeline_run_id,
                  c.input_fingerprint, c.output_fingerprint,
                  c.api_cost, c.asr_cost, c.llm_cost, c.total_cost,
                  c.cost_currency, c.cost_basis,
                  a.id, a.model, a.model_revision,
                  a.prompt_version, a.schema_version
                from research_task_cost c
                join analysis_run a on a.task_cost_id=c.id
                where c.task_key=%s
                """,
                (task_key,),
            )
            existing = cur.fetchone()
            if existing is not None:
                _assert_replay_matches(
                    existing,
                    video_id=video_id,
                    pipeline_run_id=pipeline_run_id,
                    result=result,
                    output_fingerprint=output_fingerprint,
                    costs=costs,
                    cost=cost,
                )
                return L3ResearchRecord(
                    analysis_run_id=existing[11],
                    task_cost_id=existing[0],
                    created=False,
                    research_level=max(3, int(video[0])),
                    api_cost=existing[5],
                    asr_cost=existing[6],
                    llm_cost=existing[7],
                    total_cost=existing[8],
                    cost_currency=existing[9],
                )

            cur.execute(
                """
                insert into research_task_cost(
                  task_key, task_type, task_version, pipeline_run_id,
                  video_id, status, input_fingerprint, output_fingerprint,
                  api_cost, asr_cost, llm_cost,
                  cost_currency, cost_basis, metadata
                ) values (
                  %s,%s,%s,%s,%s,'completed',%s,%s,
                  %s,%s,%s,%s,%s,%s
                )
                returning id, total_cost
                """,
                (
                    task_key,
                    L3_ANALYSIS_TYPE,
                    result.schema_version,
                    pipeline_run_id,
                    video_id,
                    result.input_fingerprint,
                    output_fingerprint,
                    *costs,
                    cost.currency,
                    cost.basis,
                    Jsonb(
                        {
                            "promotion_gate_verified": True,
                            "semantic_inference": True,
                            "mechanism_hypotheses_are_inferences": True,
                            "privacy_reviewed": True,
                            "raw_evidence_stored": False,
                        }
                    ),
                ),
            )
            task_cost_id, total_cost = cur.fetchone()

            cur.execute(
                """
                insert into analysis_run(
                  video_id, analysis_type, analysis_level, status,
                  model, model_revision, prompt_version, schema_version,
                  input_fingerprint, output_fingerprint,
                  input_refs, output, cost_amount, cost_currency,
                  task_cost_id
                ) values (
                  %s,%s,'L3','completed',
                  %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                )
                returning id
                """,
                (
                    video_id,
                    L3_ANALYSIS_TYPE,
                    result.model_id,
                    result.model_revision,
                    result.prompt_version,
                    result.schema_version,
                    result.input_fingerprint,
                    output_fingerprint,
                    Jsonb(
                        {
                            "fingerprint": result.input_fingerprint,
                            "evidence_modalities": list(result.evidence_modalities),
                            "raw_evidence_stored": False,
                        }
                    ),
                    Jsonb(_public_output(result)),
                    total_cost,
                    cost.currency,
                    task_cost_id,
                ),
            )
            analysis_run_id = cur.fetchone()[0]
            cur.execute(
                """
                update source_video
                set research_level=greatest(research_level, 3),
                    updated_at=now()
                where id=%s
                """,
                (video_id,),
            )
            conn.commit()

        return L3ResearchRecord(
            analysis_run_id=analysis_run_id,
            task_cost_id=task_cost_id,
            created=True,
            research_level=3,
            api_cost=costs[0],
            asr_cost=costs[1],
            llm_cost=costs[2],
            total_cost=total_cost,
            cost_currency=cost.currency,
        )


def _validate_task_key(task_key: str) -> None:
    if not task_key.strip():
        raise ValueError("task_key is required")
    if len(task_key) > 200:
        raise ValueError("task_key is too long")


def _validate_result(result: L3ResearchResult) -> None:
    required = {
        "model_id": result.model_id,
        "model_revision": result.model_revision,
        "prompt_version": result.prompt_version,
        "schema_version": result.schema_version,
        "input_fingerprint": result.input_fingerprint,
    }
    missing = [name for name, value in required.items() if not value.strip()]
    if missing:
        raise ValueError(f"required L3 version fields are missing: {missing}")
    if result.schema_version != L3_SCHEMA_VERSION:
        raise ValueError("unsupported L3 schema version")
    if not result.privacy_reviewed:
        raise ValueError("L3 result requires an explicit privacy review")

    modalities = set(result.evidence_modalities)
    if not modalities:
        raise ValueError("at least one evidence modality is required")
    unknown = modalities - EVIDENCE_MODALITIES
    if unknown:
        raise ValueError("unsupported evidence modality")
    if len(modalities) != len(result.evidence_modalities):
        raise ValueError("evidence modalities must be unique")

    sections = {
        "narrative_structure": result.narrative_structure,
        "hook_functions": result.hook_functions,
        "comment_semantics": result.comment_semantics,
        "case_comparisons": result.case_comparisons,
        "mechanism_hypotheses": result.mechanism_hypotheses,
        "ip_fit": result.ip_fit,
        "limitations": result.limitations,
    }
    for name, items in sections.items():
        if not items:
            raise ValueError(f"{name} requires at least one item")
        if len(items) > MAX_SECTION_ITEMS:
            raise ValueError(f"{name} exceeds the item limit")
        for item in items:
            if not item.strip():
                raise ValueError(f"{name} cannot contain blank items")
            if len(item) > MAX_ITEM_CHARS:
                raise ValueError(f"{name} item is too long")


def _validate_cost(
    cost: TaskCost,
) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    values = tuple(
        None if value is None else Decimal(str(value))
        for value in (cost.api_cost, cost.asr_cost, cost.llm_cost)
    )
    if any(value is not None and value < 0 for value in values):
        raise ValueError("task costs cannot be negative")
    if not cost.currency.strip():
        raise ValueError("cost currency is required")
    if cost.basis not in COST_BASES:
        raise ValueError("invalid cost basis")
    if cost.basis in {"actual", "estimated"} and any(
        value is None for value in values
    ):
        raise ValueError(f"{cost.basis} cost basis requires all cost fields")
    if cost.basis == "unknown" and all(value is not None for value in values):
        raise ValueError("unknown cost basis requires at least one unknown field")
    return values


def _public_output(result: L3ResearchResult) -> dict[str, Any]:
    return {
        "narrative_structure": list(result.narrative_structure),
        "hook_functions": list(result.hook_functions),
        "comment_semantics": list(result.comment_semantics),
        "case_comparisons": list(result.case_comparisons),
        "mechanism_hypotheses": list(result.mechanism_hypotheses),
        "ip_fit": list(result.ip_fit),
        "limitations": list(result.limitations),
        "mechanism_hypotheses_are_inferences": True,
        "privacy_reviewed": True,
    }


def _output_fingerprint(result: L3ResearchResult) -> str:
    payload = asdict(result)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _assert_replay_matches(
    existing: tuple[Any, ...],
    *,
    video_id: UUID | str,
    pipeline_run_id: UUID | str | None,
    result: L3ResearchResult,
    output_fingerprint: str,
    costs: tuple[Decimal | None, Decimal | None, Decimal | None],
    cost: TaskCost,
) -> None:
    expected_pipeline = (
        UUID(str(pipeline_run_id)) if pipeline_run_id is not None else None
    )
    expected = (
        UUID(str(video_id)),
        expected_pipeline,
        result.input_fingerprint,
        output_fingerprint,
        *costs,
        cost.currency,
        cost.basis,
        result.model_id,
        result.model_revision,
        result.prompt_version,
        result.schema_version,
    )
    actual = (
        existing[1],
        existing[2],
        existing[3],
        existing[4],
        existing[5],
        existing[6],
        existing[7],
        existing[9],
        existing[10],
        existing[12],
        existing[13],
        existing[14],
        existing[15],
    )
    if actual != expected:
        raise ValueError("task_key already exists with different L3 inputs or output")
