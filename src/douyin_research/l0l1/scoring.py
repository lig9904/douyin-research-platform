"""Deterministic L1 priority scoring.  No LLMs, no learned model."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

RULE_VERSION = "blackhorse-v1.0.0"


@dataclass(slots=True)
class Candidate:
    video_id: UUID
    published_at: datetime | None
    rank_value: float | None
    source_count: int | None
    signal_count: int
    snapshot_count: int
    play_count: int | None
    like_count: int | None
    comment_count: int | None
    share_count: int | None
    followers: int | None
    current_captured_at: datetime | None
    previous_captured_at: datetime | None
    previous_play_count: int | None
    previous_like_count: int | None
    previous_comment_count: int | None
    previous_share_count: int | None
    account_like_median: float | None
    account_baseline_n: int


class L1Scorer:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def score_run(
        self, run_id: UUID, *, now: datetime | None = None,
        video_ids: set[UUID] | None = None, project_id: UUID | None = None,
        subject_id: UUID | None = None,
    ) -> dict[UUID, float]:
        if (project_id is None) != (subject_id is None):
            raise ValueError("subject scoring requires project and subject")
        run_project_id = self._run_project_id(run_id)
        if run_project_id is not None:
            if project_id is None or subject_id is None:
                raise ValueError("project run scoring requires project and subject")
            if run_project_id != project_id:
                raise ValueError("project score scope does not match pipeline run")
            # A project interpretation is not a global monitoring priority.
            # Do not let an internal caller turn it into one until the project
            # score table and project decision view are introduced together.
            raise RuntimeError("project scoring requires project-specific score storage")
        if project_id is not None:
            raise ValueError("global run cannot use project score scope")
        now = now or datetime.now(timezone.utc)
        platform = self._assert_single_platform(run_id)
        candidates = self._load_candidates(
            run_id, video_ids=video_ids, project_id=project_id, subject_id=subject_id,
        )
        if not candidates:
            return {}

        raw: dict[UUID, dict[str, float | None]] = {}
        for c in candidates:
            raw[c.video_id] = {
                "source_strength": _source_strength(c.rank_value, c.source_count),
                "recency": _recency(c.published_at, now),
                "follower_efficiency": _follower_efficiency(c),
                "velocity": _velocity(c),
                "account_outlier": _account_outlier(c),
            }

        percentiles: dict[str, dict[UUID, float]] = {}
        for name in ("source_strength", "recency", "follower_efficiency", "velocity", "account_outlier"):
            values = {vid: feats[name] for vid, feats in raw.items() if feats[name] is not None}
            percentiles[name] = _percentiles(values)

        scores: dict[UUID, float] = {}
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            for c in candidates:
                if project_id is not None and subject_id is not None:
                    # Lock the current interpretation in the same transaction
                    # as the score write. A manual correction that won the
                    # race before this point makes the candidate ineligible;
                    # one arriving after this lock is serialized after the
                    # completed score rather than being silently interleaved.
                    cur.execute(
                        """select decision from project_video_subject_relevance
                           where project_id=%s and subject_id=%s and video_id=%s
                           for share""",
                        (project_id, subject_id, c.video_id),
                    )
                    gate = cur.fetchone()
                    if gate is None or gate[0] != "relevant":
                        continue
                components: dict[str, Any] = {
                    "platform": platform,
                    "raw": raw[c.video_id],
                    "percentiles": {},
                }
                ps: list[float] = []
                for name in percentiles:
                    if c.video_id in percentiles[name]:
                        p = percentiles[name][c.video_id]
                        ps.append(p)
                        components["percentiles"][name] = p

                base = statistics.median(ps) if ps else 0.0
                bonus = min(0.10, max(0, c.signal_count - 1) * 0.02)
                score = round(min(1.0, base + bonus) * 100, 2)
                confidence = _confidence(c)
                components.update(
                    {
                        "signal_count": c.signal_count,
                        "snapshot_count": c.snapshot_count,
                        "account_baseline_n": c.account_baseline_n,
                        "multi_signal_bonus": bonus,
                        "data_confidence": confidence,
                    }
                )

                next_due = _next_due(score, now)
                cur.execute(
                    """
                    insert into video_score(video_id, score_type, score, rule_version, components)
                    values (%s,'priority',%s,%s,%s)
                    """,
                    (c.video_id, score, RULE_VERSION, Jsonb(components)),
                )
                cur.execute(
                    """
                    update source_video
                    set research_level=greatest(research_level,1),
                        monitoring_status='observe',
                        monitoring_priority=%s,
                        next_due_at=%s
                    where id=%s
                    """,
                    (score, next_due, c.video_id),
                )
                cur.execute(
                    """
                    update pipeline_run_item
                    set stage='L1', outcome='scored',
                        metadata=metadata || %s
                    where run_id=%s and entity_type='video' and entity_id=%s
                    """,
                    (Jsonb({"score": score, "confidence": confidence}), run_id, c.video_id),
                )
                scores[c.video_id] = score

            conn.commit()
        return scores

    def _run_project_id(self, run_id: UUID) -> UUID | None:
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("select project_id from pipeline_run where id=%s", (run_id,))
            row = cur.fetchone()
        if row is None:
            raise ValueError(f"pipeline_run not found: {run_id}")
        return row[0]

    def _assert_single_platform(self, run_id: UUID) -> str | None:
        sql = """
        select pr.platform, array_agg(distinct sv.platform order by sv.platform)
        from pipeline_run pr
        left join pipeline_run_item pri
          on pri.run_id=pr.id and pri.entity_type='video'
        left join source_video sv on sv.id=pri.entity_id
        where pr.id=%s
        group by pr.platform
        """
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(sql, (run_id,))
            row = cur.fetchone()
        if row is None:
            raise ValueError(f"pipeline_run not found: {run_id}")
        declared, observed = row
        observed = [x for x in (observed or []) if x is not None]
        if len(observed) > 1:
            raise ValueError(
                f"cross-platform L1 scoring is not allowed: platforms={observed}"
            )
        if declared is not None and observed and declared != observed[0]:
            raise ValueError(
                f"run platform={declared} does not match observed platform={observed[0]}"
            )
        return declared or (observed[0] if observed else None)

    def _load_candidates(
        self, run_id: UUID, *, video_ids: set[UUID] | None = None,
        project_id: UUID | None = None, subject_id: UUID | None = None,
    ) -> list[Candidate]:
        sql = """
        with vids as (
          select entity_id as video_id
          from pipeline_run_item
          where run_id=%s and entity_type='video'
            and (%s::uuid[] is null or entity_id=any(%s::uuid[]))
            and (
              %s::uuid is null or exists (
                select 1 from project_video_subject_relevance relevance
                where relevance.project_id=%s::uuid and relevance.subject_id=%s::uuid
                  and relevance.video_id=pipeline_run_item.entity_id
                  and relevance.decision='relevant'
              )
            )
        ),
        current_metric as (
          select distinct on (m.video_id)
            m.*
          from metric_snapshot m
          join vids v on v.video_id=m.video_id
          order by m.video_id, m.captured_at desc, m.id desc
        ),
        previous_metric as (
          select distinct on (m.video_id)
            m.video_id, m.captured_at,
            m.play_count, m.like_count, m.comment_count, m.share_count
          from metric_snapshot m
          join current_metric cm on cm.video_id=m.video_id
          where m.id <> cm.id and m.captured_at <= cm.captured_at
          order by m.video_id, m.captured_at desc, m.id desc
        ),
        run_discovery as (
          select d.video_id,
                 min(d.rank_value) as rank_value,
                 max(nullif(d.metadata->>'source_count','')::int) as source_count
          from discovery_event d
          join vids v on v.video_id=d.video_id
          where d.metadata->>'run_id' = %s
          group by d.video_id
        ),
        signals as (
          select d.video_id, count(distinct d.source_type)::int signal_count
          from discovery_event d
          join vids v on v.video_id=d.video_id
          where d.discovered_at >= now() - interval '24 hours'
          group by d.video_id
        ),
        snap_counts as (
          select m.video_id, count(*)::int snapshot_count
          from metric_snapshot m join vids v on v.video_id=m.video_id
          group by m.video_id
        ),
        baseline as (
          select sv.account_id,
                 percentile_cont(0.5) within group (order by ms.like_count)
                   filter (where ms.like_count is not null) as like_median,
                 count(ms.like_count)::int as baseline_n
          from source_video sv
          join vids v on v.video_id=sv.id
          join source_video other on other.account_id=sv.account_id
          join lateral (
            select distinct on (m.video_id) m.video_id, m.like_count
            from metric_snapshot m
            where m.video_id=other.id
            order by m.video_id, m.captured_at desc, m.id desc
          ) ms on true
          where sv.account_id is not null
          group by sv.account_id
        )
        select
          sv.id,
          sv.published_at,
          rd.rank_value,
          rd.source_count,
          coalesce(s.signal_count,0),
          coalesce(sc.snapshot_count,0),
          cm.play_count, cm.like_count, cm.comment_count, cm.share_count,
          cm.author_follower_count,
          cm.captured_at,
          pm.captured_at,
          pm.play_count, pm.like_count, pm.comment_count, pm.share_count,
          b.like_median,
          coalesce(b.baseline_n,0)
        from source_video sv
        join vids v on v.video_id=sv.id
        left join current_metric cm on cm.video_id=sv.id
        left join previous_metric pm on pm.video_id=sv.id
        left join run_discovery rd on rd.video_id=sv.id
        left join signals s on s.video_id=sv.id
        left join snap_counts sc on sc.video_id=sv.id
        left join baseline b on b.account_id=sv.account_id
        order by sv.id
        """
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            identifiers = list(video_ids) if video_ids is not None else None
            cur.execute(
                sql,
                (run_id, identifiers, identifiers, project_id, project_id, subject_id, str(run_id)),
            )
            rows = cur.fetchall()
        return [Candidate(*row) for row in rows]


def _source_strength(rank: float | None, count: int | None) -> float | None:
    if rank is None or count is None or count <= 0:
        return None
    if count == 1:
        return 1.0
    return max(0.0, min(1.0, 1.0 - (float(rank) - 1.0) / (count - 1.0)))


def _recency(published_at: datetime | None, now: datetime) -> float | None:
    if published_at is None:
        return None
    hours = max(0.0, (now - published_at).total_seconds() / 3600)
    return max(0.0, min(1.0, 1.0 - hours / (7 * 24)))


def _follower_efficiency(c: Candidate) -> float | None:
    if c.followers is None:
        return None
    values = [x for x in (c.like_count, c.comment_count, c.share_count) if x is not None]
    if not values:
        return None
    engagement = sum(values)
    return math.log1p(engagement) - math.log1p(max(c.followers, 100))


def _velocity(c: Candidate) -> float | None:
    if c.previous_captured_at is None or c.current_captured_at is None:
        return None
    elapsed_hours = (c.current_captured_at - c.previous_captured_at).total_seconds() / 3600
    if elapsed_hours <= 0:
        return None
    deltas: list[float] = []
    for current, previous in (
        (c.play_count, c.previous_play_count),
        (c.like_count, c.previous_like_count),
        (c.comment_count, c.previous_comment_count),
        (c.share_count, c.previous_share_count),
    ):
        if current is not None and previous is not None and current >= previous:
            per_hour = (current - previous) / elapsed_hours
            deltas.append(math.log1p(per_hour))
    return statistics.mean(deltas) if deltas else None


def _account_outlier(c: Candidate) -> float | None:
    if c.account_baseline_n < 5 or c.account_like_median is None or c.like_count is None:
        return None
    return c.like_count / max(c.account_like_median, 1.0)


def _percentiles(values: dict[UUID, float | None]) -> dict[UUID, float]:
    clean = [(vid, float(v)) for vid, v in values.items() if v is not None]
    if not clean:
        return {}
    if len(clean) == 1:
        return {clean[0][0]: 0.5}
    sorted_values = sorted(v for _, v in clean)
    out: dict[UUID, float] = {}
    for vid, value in clean:
        less = sum(1 for x in sorted_values if x < value)
        equal = sum(1 for x in sorted_values if x == value)
        # Mid-rank percentile makes ties deterministic and neutral.
        rank = less + (equal - 1) / 2
        out[vid] = rank / (len(sorted_values) - 1)
    return out


def _confidence(c: Candidate) -> str:
    if c.snapshot_count >= 2 and c.signal_count >= 2 and c.account_baseline_n >= 5:
        return "high"
    if c.snapshot_count >= 2 or c.signal_count >= 2 or c.account_baseline_n >= 5:
        return "medium"
    return "low"


def _next_due(score: float, now: datetime) -> datetime:
    if score >= 80:
        return now + timedelta(hours=6)
    if score >= 60:
        return now + timedelta(hours=12)
    return now + timedelta(hours=24)
