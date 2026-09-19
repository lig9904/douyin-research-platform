"""Deterministic, quota-bounded selection of L2 evidence for L3 work."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb


PROMOTION_RULE_VERSION = "l2-l3-promotion-v1.0.0"
MAX_TOP_N = 20
DEFAULT_QUOTA_KEY = "l3"


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    video_id: UUID
    score: float | None
    candidate_rank: int | None
    outcome: str
    reason_code: str


@dataclass(frozen=True, slots=True)
class PromotionResult:
    batch_id: UUID
    pipeline_run_id: UUID
    created: bool
    quota_date: date
    requested_top_n: int
    selected_count: int
    remaining_daily_quota: int
    decisions: tuple[PromotionDecision, ...]


@dataclass(frozen=True, slots=True)
class _Candidate:
    video_id: UUID
    research_level: int
    score: Decimal | None
    already_selected_today: bool


class L3PromotionGate:
    """Record an L3 selection plan without running ASR, APIs, or models.

    Selection is restricted to L2 videos, ordered by the latest deterministic
    priority score, and bounded by both a hard Top-N cap and a preconfigured
    per-platform daily quota. Selection never marks a video as completed L3.
    """

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def configure_daily_quota(
        self,
        *,
        platform: str,
        max_items: int,
        quota_date: date | None = None,
        quota_key: str = DEFAULT_QUOTA_KEY,
    ) -> None:
        if max_items < 0:
            raise ValueError("max_items cannot be negative")
        quota_date = quota_date or date.today()
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into daily_research_quota(
                  quota_date, platform, quota_key, max_items
                ) values (%s,%s,%s,%s)
                on conflict(quota_date, platform, quota_key)
                do update set max_items=excluded.max_items, updated_at=now()
                """,
                (quota_date, platform, quota_key, max_items),
            )
            conn.commit()

    def promote(
        self,
        source_run_id: UUID | str,
        *,
        top_n: int,
        min_score: float = 0,
        quota_date: date | None = None,
        quota_key: str = DEFAULT_QUOTA_KEY,
        triggered_by: str = "system",
    ) -> PromotionResult:
        if top_n <= 0:
            raise ValueError("top_n must be positive")
        if top_n > MAX_TOP_N:
            raise ValueError(f"top_n cannot exceed hard cap {MAX_TOP_N}")
        if min_score < 0 or min_score > 100:
            raise ValueError("min_score must be between 0 and 100")
        quota_date = quota_date or date.today()
        score_floor = Decimal(str(min_score))

        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "select platform, status from pipeline_run where id=%s",
                (source_run_id,),
            )
            source = cur.fetchone()
            if source is None:
                raise ValueError("source pipeline run does not exist")
            platform, source_status = source
            if platform is None:
                raise ValueError("source pipeline run must declare a platform")
            if source_status != "success":
                raise ValueError("source pipeline run must be successful")

            cur.execute(
                """
                select max_items, used_items
                from daily_research_quota
                where quota_date=%s and platform=%s and quota_key=%s
                for update
                """,
                (quota_date, platform, quota_key),
            )
            quota = cur.fetchone()
            if quota is None:
                raise ValueError("daily L3 promotion quota is not configured")
            max_items, used_items = quota

            candidates = self._load_candidates(
                cur,
                source_run_id=source_run_id,
                quota_date=quota_date,
            )
            fingerprint = _candidate_fingerprint(candidates)
            cur.execute(
                """
                select id, pipeline_run_id, selected_count
                from research_promotion_batch
                where source_run_id=%s
                  and quota_date=%s
                  and rule_version=%s
                  and requested_top_n=%s
                  and min_score=%s
                  and candidate_fingerprint=%s
                """,
                (
                    source_run_id,
                    quota_date,
                    PROMOTION_RULE_VERSION,
                    top_n,
                    score_floor,
                    fingerprint,
                ),
            )
            existing = cur.fetchone()
            if existing is not None:
                batch_id, pipeline_run_id, selected_count = existing
                decisions = self._load_decisions(cur, batch_id)
                return PromotionResult(
                    batch_id=batch_id,
                    pipeline_run_id=pipeline_run_id,
                    created=False,
                    quota_date=quota_date,
                    requested_top_n=top_n,
                    selected_count=selected_count,
                    remaining_daily_quota=max(0, max_items - used_items),
                    decisions=decisions,
                )

            decisions = _decide(
                candidates,
                top_n=top_n,
                remaining_quota=max(0, max_items - used_items),
                min_score=score_floor,
            )
            selected_count = sum(item.outcome == "selected" for item in decisions)
            batch_id = uuid4()
            pipeline_run_id = uuid4()

            cur.execute(
                """
                insert into pipeline_run(
                  id, run_type, run_version, platform, status, triggered_by,
                  started_at, finished_at, input_count, output_count,
                  promoted_l3_count, api_cost, asr_cost, llm_cost, summary
                ) values (
                  %s,'l2_l3_promotion',%s,%s,'success',%s,
                  now(),now(),%s,%s,%s,0,0,0,%s
                )
                """,
                (
                    pipeline_run_id,
                    PROMOTION_RULE_VERSION,
                    platform,
                    triggered_by,
                    len(candidates),
                    selected_count,
                    selected_count,
                    Jsonb(
                        {
                            "source_run_id": str(source_run_id),
                            "quota_date": quota_date.isoformat(),
                            "quota_key": quota_key,
                            "requested_top_n": top_n,
                            "min_score": float(score_floor),
                            "candidate_fingerprint": fingerprint,
                            "selection_only": True,
                            "external_calls": 0,
                            "llm_calls": 0,
                        }
                    ),
                ),
            )
            cur.execute(
                """
                insert into research_promotion_batch(
                  id, pipeline_run_id, source_run_id, quota_date, platform,
                  quota_key, target_level, rule_version, requested_top_n,
                  min_score, candidate_fingerprint, selected_count
                ) values (%s,%s,%s,%s,%s,%s,3,%s,%s,%s,%s,%s)
                """,
                (
                    batch_id,
                    pipeline_run_id,
                    source_run_id,
                    quota_date,
                    platform,
                    quota_key,
                    PROMOTION_RULE_VERSION,
                    top_n,
                    score_floor,
                    fingerprint,
                    selected_count,
                ),
            )
            for item in decisions:
                cur.execute(
                    """
                    insert into research_promotion_decision(
                      batch_id, video_id, quota_date, target_level,
                      candidate_rank, score, outcome, reason_code, metadata
                    ) values (%s,%s,%s,3,%s,%s,%s,%s,%s)
                    """,
                    (
                        batch_id,
                        item.video_id,
                        quota_date,
                        item.candidate_rank,
                        item.score,
                        item.outcome,
                        item.reason_code,
                        Jsonb({"selection_only": True}),
                    ),
                )
                cur.execute(
                    """
                    insert into pipeline_run_item(
                      run_id, entity_type, entity_id, stage,
                      outcome, reason_code, metadata
                    ) values (%s,'video',%s,'L3_GATE',%s,%s,%s)
                    """,
                    (
                        pipeline_run_id,
                        item.video_id,
                        item.outcome,
                        item.reason_code,
                        Jsonb(
                            {
                                "candidate_rank": item.candidate_rank,
                                "score": item.score,
                            }
                        ),
                    ),
                )

            cur.execute(
                """
                update daily_research_quota
                set used_items=used_items+%s, updated_at=now()
                where quota_date=%s and platform=%s and quota_key=%s
                """,
                (selected_count, quota_date, platform, quota_key),
            )
            conn.commit()

        return PromotionResult(
            batch_id=batch_id,
            pipeline_run_id=pipeline_run_id,
            created=True,
            quota_date=quota_date,
            requested_top_n=top_n,
            selected_count=selected_count,
            remaining_daily_quota=max(0, max_items - used_items - selected_count),
            decisions=decisions,
        )

    @staticmethod
    def _load_candidates(
        cur,
        *,
        source_run_id: UUID | str,
        quota_date: date,
    ) -> list[_Candidate]:
        cur.execute(
            """
            with latest_score as (
              select distinct on (s.video_id)
                s.video_id, s.score
              from video_score s
              where s.score_type='priority'
              order by s.video_id, s.calculated_at desc, s.id desc
            ),
            selected_today as (
              select distinct d.video_id
              from research_promotion_decision d
              where d.quota_date=%s
                and d.target_level=3
                and d.outcome='selected'
            )
            select
              v.id,
              v.research_level,
              s.score,
              (st.video_id is not null) as already_selected_today
            from pipeline_run_item i
            join source_video v on v.id=i.entity_id
            left join latest_score s on s.video_id=v.id
            left join selected_today st on st.video_id=v.id
            where i.run_id=%s and i.entity_type='video'
            order by v.id
            """,
            (quota_date, source_run_id),
        )
        return [_Candidate(*row) for row in cur.fetchall()]

    @staticmethod
    def _load_decisions(cur, batch_id: UUID) -> tuple[PromotionDecision, ...]:
        cur.execute(
            """
            select video_id, score, candidate_rank, outcome, reason_code
            from research_promotion_decision
            where batch_id=%s
            order by candidate_rank nulls last, video_id
            """,
            (batch_id,),
        )
        return tuple(
            PromotionDecision(
                video_id=row[0],
                score=float(row[1]) if row[1] is not None else None,
                candidate_rank=row[2],
                outcome=row[3],
                reason_code=row[4],
            )
            for row in cur.fetchall()
        )


def _decide(
    candidates: list[_Candidate],
    *,
    top_n: int,
    remaining_quota: int,
    min_score: Decimal,
) -> tuple[PromotionDecision, ...]:
    eligible = sorted(
        (
            item
            for item in candidates
            if not item.already_selected_today
            and item.research_level >= 2
            and item.score is not None
            and item.score >= min_score
        ),
        key=lambda item: (-item.score, str(item.video_id)),
    )
    ranks = {item.video_id: index for index, item in enumerate(eligible, start=1)}
    selected_limit = min(top_n, remaining_quota)

    decisions: list[PromotionDecision] = []
    for item in candidates:
        rank = ranks.get(item.video_id)
        if item.already_selected_today:
            outcome, reason = "skipped", "already_selected_today"
        elif item.research_level < 2:
            outcome, reason = "skipped", "l2_required"
        elif item.score is None:
            outcome, reason = "skipped", "priority_score_required"
        elif item.score < min_score:
            outcome, reason = "skipped", "below_min_score"
        elif rank is not None and rank <= selected_limit:
            outcome, reason = "selected", "top_n_and_daily_quota"
        elif rank is not None and rank > top_n:
            outcome, reason = "skipped", "top_n_limit"
        else:
            outcome, reason = "skipped", "daily_quota_exhausted"
        decisions.append(
            PromotionDecision(
                video_id=item.video_id,
                score=float(item.score) if item.score is not None else None,
                candidate_rank=rank,
                outcome=outcome,
                reason_code=reason,
            )
        )
    return tuple(
        sorted(
            decisions,
            key=lambda item: (
                item.candidate_rank is None,
                item.candidate_rank or 0,
                str(item.video_id),
            ),
        )
    )


def _candidate_fingerprint(candidates: list[_Candidate]) -> str:
    payload: list[dict[str, Any]] = [
        {
            "video_id": str(item.video_id),
            "research_level": item.research_level,
            "score": str(item.score) if item.score is not None else None,
        }
        for item in candidates
    ]
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
