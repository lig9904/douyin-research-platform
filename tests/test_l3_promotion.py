from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from uuid import UUID

import psycopg
import pytest

from douyin_research.l2 import L3PromotionGate, MAX_TOP_N


DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not configured")
QUOTA_DATE = date(2026, 9, 19)


def _clear() -> None:
    assert DSN
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("delete from research_promotion_decision")
        cur.execute("delete from research_promotion_batch")
        cur.execute("delete from daily_research_quota")
        cur.execute("delete from pipeline_run")
        cur.execute("delete from source_video")
        conn.commit()


def _source_run(
    specs: list[tuple[str, int, int | None]],
    *,
    status: str = "success",
) -> tuple[UUID, dict[str, UUID]]:
    assert DSN
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into pipeline_run(run_type, run_version, platform, status)
            values ('synthetic-source', 'test', 'douyin', %s)
            returning id
            """,
            (status,),
        )
        run_id = cur.fetchone()[0]
        video_ids: dict[str, UUID] = {}
        for key, level, score in specs:
            cur.execute(
                """
                insert into source_video(
                  platform, platform_video_id, research_level
                ) values ('douyin', %s, %s)
                returning id
                """,
                (f"synthetic-promotion-{key}", level),
            )
            video_id = cur.fetchone()[0]
            video_ids[key] = video_id
            cur.execute(
                """
                insert into pipeline_run_item(
                  run_id, entity_type, entity_id, stage, outcome
                ) values (%s, 'video', %s, 'L1', 'scored')
                """,
                (run_id, video_id),
            )
            if score is not None:
                cur.execute(
                    """
                    insert into video_score(
                      video_id, score_type, score, rule_version
                    ) values (%s, 'priority', %s, 'synthetic-v1')
                    """,
                    (video_id, score),
                )
        conn.commit()
    return run_id, video_ids


def test_promotion_is_ranked_bounded_audited_and_replay_safe() -> None:
    assert DSN
    _clear()
    run_id, video_ids = _source_run(
        [
            ("high", 2, 90),
            ("medium", 2, 80),
            ("below-floor", 2, 70),
            ("not-l2", 1, 100),
            ("missing-score", 2, None),
        ]
    )
    gate = L3PromotionGate(DSN)
    gate.configure_daily_quota(
        platform="douyin",
        max_items=1,
        quota_date=QUOTA_DATE,
    )

    first = gate.promote(
        run_id,
        top_n=3,
        min_score=75,
        quota_date=QUOTA_DATE,
        triggered_by="synthetic-test",
    )
    replay = gate.promote(
        run_id,
        top_n=3,
        min_score=75,
        quota_date=QUOTA_DATE,
        triggered_by="synthetic-test",
    )

    assert first.created is True
    assert first.selected_count == 1
    assert first.remaining_daily_quota == 0
    assert replay.created is False
    assert replay.batch_id == first.batch_id
    assert replay.pipeline_run_id == first.pipeline_run_id
    assert replay.selected_count == 1

    decisions = {item.video_id: item for item in first.decisions}
    assert decisions[video_ids["high"]].outcome == "selected"
    assert decisions[video_ids["high"]].candidate_rank == 1
    assert decisions[video_ids["medium"]].reason_code == "daily_quota_exhausted"
    assert decisions[video_ids["below-floor"]].reason_code == "below_min_score"
    assert decisions[video_ids["not-l2"]].reason_code == "l2_required"
    assert decisions[video_ids["missing-score"]].reason_code == "priority_score_required"

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select used_items
            from daily_research_quota
            where quota_date=%s and platform='douyin' and quota_key='l3'
            """,
            (QUOTA_DATE,),
        )
        assert cur.fetchone()[0] == 1
        cur.execute(
            """
            select promoted_l3_count, api_cost, asr_cost, llm_cost,
                   summary->>'selection_only', summary->>'external_calls'
            from pipeline_run
            where id=%s
            """,
            (first.pipeline_run_id,),
        )
        assert cur.fetchone() == (
            1,
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
            "true",
            "0",
        )
        cur.execute(
            "select research_level from source_video where id=%s",
            (video_ids["high"],),
        )
        assert cur.fetchone()[0] == 2


def test_promotion_fails_closed_without_quota_or_successful_source() -> None:
    assert DSN
    _clear()
    run_id, _ = _source_run([("one", 2, 90)])
    gate = L3PromotionGate(DSN)

    with pytest.raises(ValueError, match="quota is not configured"):
        gate.promote(run_id, top_n=1, quota_date=QUOTA_DATE)

    failed_run_id, _ = _source_run([("failed", 2, 90)], status="failed")
    gate.configure_daily_quota(
        platform="douyin",
        max_items=1,
        quota_date=QUOTA_DATE,
    )
    with pytest.raises(ValueError, match="must be successful"):
        gate.promote(failed_run_id, top_n=1, quota_date=QUOTA_DATE)


@pytest.mark.parametrize("top_n", [0, -1, MAX_TOP_N + 1])
def test_promotion_rejects_invalid_top_n(top_n: int) -> None:
    assert DSN
    _clear()
    run_id, _ = _source_run([("one", 2, 90)])
    gate = L3PromotionGate(DSN)

    with pytest.raises(ValueError, match="top_n"):
        gate.promote(run_id, top_n=top_n, quota_date=QUOTA_DATE)


def test_top_n_ties_are_deterministic_and_do_not_mark_l3_complete() -> None:
    assert DSN
    _clear()
    run_id, video_ids = _source_run(
        [("tie-a", 2, 90), ("tie-b", 2, 90), ("tie-c", 2, 90)]
    )
    gate = L3PromotionGate(DSN)
    gate.configure_daily_quota(
        platform="douyin",
        max_items=3,
        quota_date=QUOTA_DATE,
    )

    result = gate.promote(run_id, top_n=2, quota_date=QUOTA_DATE)

    expected = sorted(video_ids.values(), key=str)[:2]
    selected = [
        item.video_id
        for item in result.decisions
        if item.outcome == "selected"
    ]
    assert selected == expected
    assert result.selected_count == 2
    skipped = [item for item in result.decisions if item.outcome == "skipped"]
    assert len(skipped) == 1
    assert skipped[0].reason_code == "top_n_limit"

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select count(*)
            from source_video
            where id=any(%s) and research_level=3
            """,
            (list(video_ids.values()),),
        )
        assert cur.fetchone()[0] == 0
