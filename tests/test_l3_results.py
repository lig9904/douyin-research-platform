from __future__ import annotations

import os
from decimal import Decimal
from uuid import UUID

import psycopg
import pytest

from douyin_research.l2 import L3PromotionGate, TaskCost
from douyin_research.l3 import (
    L3_ANALYSIS_TYPE,
    L3_SCHEMA_VERSION,
    L3ResearchResult,
    L3ResearchStore,
)


DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not configured")


def _clear() -> None:
    assert DSN
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("delete from analysis_run")
        cur.execute("delete from research_task_cost")
        cur.execute("delete from research_promotion_decision")
        cur.execute("delete from research_promotion_batch")
        cur.execute("delete from daily_research_quota")
        cur.execute("delete from pipeline_run")
        cur.execute("delete from source_video")
        conn.commit()


def _video(*, selected: bool, key: str = "main") -> tuple[UUID, UUID | None]:
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
            insert into source_video(
              platform, platform_video_id, research_level
            ) values ('douyin', %s, 2)
            returning id
            """,
            (f"synthetic-l3-result-{key}",),
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

    if not selected:
        return video_id, None

    gate = L3PromotionGate(DSN)
    gate.configure_daily_quota(platform="douyin", max_items=1)
    promotion = gate.promote(source_run_id, top_n=1)
    return video_id, promotion.pipeline_run_id


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
        "narrative_structure": ("Opening tension followed by demonstration.",),
        "hook_functions": ("Creates an information gap.",),
        "comment_semantics": ("The sampled discussion asks for implementation detail.",),
        "case_comparisons": ("The synthetic comparison differs in pacing.",),
        "mechanism_hypotheses": ("Specificity may improve retention.",),
        "ip_fit": ("Adapt the structure without copying source wording.",),
        "limitations": ("This is model inference from a bounded evidence set.",),
        "privacy_reviewed": True,
    }
    values.update(overrides)
    return L3ResearchResult(**values)


def test_selected_result_is_versioned_costed_and_replay_safe() -> None:
    assert DSN
    _clear()
    video_id, promotion_run_id = _video(selected=True)
    store = L3ResearchStore(DSN)
    cost = TaskCost(
        api_cost=Decimal("0"),
        asr_cost=Decimal("0"),
        llm_cost=Decimal("0.25"),
        currency="CNY",
        basis="actual",
    )

    first = store.ingest(
        video_id,
        task_key="synthetic-l3-task",
        result=_result(),
        cost=cost,
        pipeline_run_id=promotion_run_id,
    )
    replay = store.ingest(
        video_id,
        task_key="synthetic-l3-task",
        result=_result(),
        cost=cost,
        pipeline_run_id=promotion_run_id,
    )

    assert first.created is True
    assert replay.created is False
    assert replay.analysis_run_id == first.analysis_run_id
    assert replay.task_cost_id == first.task_cost_id
    assert first.total_cost == Decimal("0.25")
    assert first.research_level == 3

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select
              a.analysis_type, a.analysis_level, a.status,
              a.model, a.model_revision, a.prompt_version, a.schema_version,
              a.output->>'mechanism_hypotheses_are_inferences',
              a.output->>'privacy_reviewed',
              a.input_refs ? 'raw_text',
              c.api_cost, c.asr_cost, c.llm_cost, c.total_cost,
              c.metadata->>'promotion_gate_verified',
              c.metadata->>'raw_evidence_stored'
            from analysis_run a
            join research_task_cost c on c.id=a.task_cost_id
            where a.id=%s
            """,
            (first.analysis_run_id,),
        )
        assert cur.fetchone() == (
            L3_ANALYSIS_TYPE,
            "L3",
            "completed",
            "synthetic-model",
            "revision-1",
            "l3-prompt-v1",
            L3_SCHEMA_VERSION,
            "true",
            "true",
            False,
            Decimal("0"),
            Decimal("0"),
            Decimal("0.25"),
            Decimal("0.25"),
            "true",
            "false",
        )
        cur.execute(
            "select research_level from source_video where id=%s",
            (video_id,),
        )
        assert cur.fetchone()[0] == 3


def test_unselected_video_fails_closed_without_writes() -> None:
    assert DSN
    _clear()
    video_id, _ = _video(selected=False)
    store = L3ResearchStore(DSN)

    with pytest.raises(ValueError, match="not selected"):
        store.ingest(
            video_id,
            task_key="synthetic-unselected-task",
            result=_result(),
            cost=TaskCost(0, 0, 0, "CNY", "actual"),
        )

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("select count(*) from analysis_run")
        assert cur.fetchone()[0] == 0
        cur.execute("select count(*) from research_task_cost")
        assert cur.fetchone()[0] == 0


def test_null_and_zero_costs_keep_distinct_meaning() -> None:
    assert DSN
    _clear()
    video_id, _ = _video(selected=True, key="nullable-cost")
    record = L3ResearchStore(DSN).ingest(
        video_id,
        task_key="synthetic-null-cost-task",
        result=_result(input_fingerprint="nullable-cost-input"),
        cost=TaskCost(
            api_cost=None,
            asr_cost=Decimal("0"),
            llm_cost=None,
            currency="CNY",
            basis="mixed",
        ),
    )

    assert record.api_cost is None
    assert record.asr_cost == Decimal("0")
    assert record.llm_cost is None
    assert record.total_cost is None


def test_changed_replay_is_rejected() -> None:
    assert DSN
    _clear()
    video_id, _ = _video(selected=True, key="changed-replay")
    store = L3ResearchStore(DSN)
    cost = TaskCost(0, 0, Decimal("0.1"), "CNY", "actual")
    store.ingest(
        video_id,
        task_key="synthetic-replay-task",
        result=_result(input_fingerprint="replay-input"),
        cost=cost,
    )

    with pytest.raises(ValueError, match="different L3 inputs or output"):
        store.ingest(
            video_id,
            task_key="synthetic-replay-task",
            result=_result(
                input_fingerprint="replay-input",
                hook_functions=("Changed model output.",),
            ),
            cost=cost,
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"privacy_reviewed": False}, "privacy review"),
        ({"schema_version": "unversioned"}, "unsupported"),
        ({"evidence_modalities": ()}, "evidence modality"),
        ({"mechanism_hypotheses": ()}, "requires at least one"),
        ({"evidence_modalities": ("metadata", "unknown")}, "unsupported"),
    ],
)
def test_result_contract_rejects_incomplete_or_unreviewed_output(
    overrides, message
) -> None:
    assert DSN
    _clear()
    video_id, _ = _video(selected=True, key=message.replace(" ", "-"))
    with pytest.raises(ValueError, match=message):
        L3ResearchStore(DSN).ingest(
            video_id,
            task_key=f"synthetic-invalid-{len(message)}",
            result=_result(**overrides),
            cost=TaskCost(0, 0, 0, "CNY", "actual"),
        )
