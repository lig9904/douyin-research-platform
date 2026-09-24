# py: ==3.14.*
#requirements:
#psycopg[binary]==3.3.6

"""Read the current actor's project subjects, rule terms, and review queue."""

from __future__ import annotations

import os
import re
from datetime import datetime
from decimal import Decimal
from typing import Any, TypedDict
from uuid import UUID

import psycopg
from psycopg.rows import dict_row


class postgresql(TypedDict):
    host: str
    port: int
    user: str
    password: str
    dbname: str
    sslmode: str


_EMAIL = re.compile(r"^[^\s@]{1,128}@[^\s@]{1,120}$")


def _actor() -> str:
    actor = os.environ.get("WM_END_USER_EMAIL", "").strip().lower()
    if not _EMAIL.fullmatch(actor) or len(actor) > 254:
        raise PermissionError("RESEARCH_ACTION_IDENTITY_REQUIRED")
    return actor


def _uuid(value: object) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise ValueError("PROJECT_ID_INVALID") from None


def _json(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (UUID, datetime)):
        return str(value) if isinstance(value, UUID) else value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json(item) for item in value]
    return value


def _connect(db: postgresql):
    options: dict[str, Any] = {
        "host": db["host"], "port": int(db.get("port", 5432)),
        "user": db["user"], "password": db["password"],
        "dbname": db["dbname"], "sslmode": db.get("sslmode", "prefer"),
    }
    if db.get("options"):
        options["options"] = db["options"]
    return psycopg.connect(**options, row_factory=dict_row)


def main(db: postgresql, project_id: str, review_limit: int = 100):
    actor = _actor()
    project = _uuid(project_id)
    limit = max(1, min(int(review_limit), 100))
    try:
        with _connect(db) as conn, conn.cursor() as cur:
            cur.execute("set transaction isolation level repeatable read read only")
            cur.execute("select project_actor_can_read(%s::uuid,%s) as allowed", (project, actor))
            if not bool((cur.fetchone() or {}).get("allowed")):
                raise PermissionError("RESEARCH_PROJECT_ACCESS_DENIED")
            cur.execute(
                """
                select subject.id, subject.name, subject.subject_type, subject.status,
                  coalesce(jsonb_agg(jsonb_build_object(
                    'id', term.id, 'term', term.term, 'term_type', term.term_type
                  ) order by term.term_type, term.term)
                  filter (where term.id is not null), '[]'::jsonb) as terms,
                  coalesce(relevance.relevant_count, 0)::int as relevant_count,
                  coalesce(relevance.pending_count, 0)::int as pending_count,
                  coalesce(relevance.irrelevant_count, 0)::int as irrelevant_count
                from research_subject subject
                left join research_subject_term term
                  on term.subject_id=subject.id and term.project_id=subject.project_id
                 and term.status='active'
                left join lateral (
                  select count(*) filter (where decision='relevant') as relevant_count,
                         count(*) filter (where decision='pending') as pending_count,
                         count(*) filter (where decision='irrelevant') as irrelevant_count
                  from project_video_subject_relevance
                  where project_id=subject.project_id and subject_id=subject.id
                    and project_video_can_read(subject.project_id,%s,video_id)
                ) relevance on true
                where subject.project_id=%s and subject.status='active'
                group by subject.id, subject.name, subject.subject_type, subject.status,
                         relevance.relevant_count, relevance.pending_count, relevance.irrelevant_count
                order by lower(subject.name), subject.id
                """,
                (actor, project),
            )
            subjects = [_json(dict(row)) for row in cur.fetchall()]
            cur.execute(
                """
                select relation.project_id, relation.subject_id, relation.video_id,
                  relation.decision, relation.decision_source, relation.rule_version,
                  relation.run_id,
                  relation.match_detail, relation.reviewed_by, relation.reviewed_at,
                  relation.updated_at, subject.name as subject_name,
                  video.platform_video_id, coalesce(video.title, video.description, '(无标题)') as title,
                  account.nickname as account_name
                from project_video_subject_relevance relation
                join research_subject subject
                  on subject.id=relation.subject_id and subject.project_id=relation.project_id
                join source_video video on video.id=relation.video_id
                left join source_account account on account.id=video.account_id
                where relation.project_id=%s
                  and project_video_can_read(relation.project_id,%s,relation.video_id)
                order by case relation.decision when 'pending' then 0 when 'irrelevant' then 1 else 2 end,
                         relation.updated_at desc, relation.video_id
                limit %s
                """,
                (project, actor, limit),
            )
            review_queue = [_json(dict(row)) for row in cur.fetchall()]
            cur.execute(
                """
                with eligible as (
                  select score.id, score.subject_id, score.video_id, score.score,
                    score.rule_version, score.created_at, score.source_run_id,
                    score.components->>'data_confidence' as confidence,
                    video.platform_video_id,
                    coalesce(video.title, video.description, '(无标题)') as title,
                    account.nickname as account_name
                  from project_video_subject_score score
                  join project_video_subject_relevance relevance
                    on relevance.project_id=score.project_id
                   and relevance.subject_id=score.subject_id
                   and relevance.video_id=score.video_id
                   and relevance.decision='relevant'
                   and score.created_at>=relevance.updated_at
                  join research_subject subject
                    on subject.id=score.subject_id and subject.project_id=score.project_id
                   and subject.status='active'
                  join project_video_inclusion inclusion
                    on inclusion.project_id=score.project_id and inclusion.video_id=score.video_id
                   and inclusion.status in ('candidate','shortlisted','accepted')
                  join source_video video on video.id=score.video_id
                   and video.availability_status='available'
                  left join source_account account on account.id=video.account_id
                  where score.project_id=%s
                    and project_video_can_read(score.project_id,%s,score.video_id)
                ), latest_batch as (
                  select distinct on (subject_id) subject_id, source_run_id
                  from eligible
                  order by subject_id, created_at desc, id desc
                ), ranked as (
                  select eligible.*,
                    row_number() over (
                      partition by eligible.subject_id
                      order by eligible.score desc, eligible.created_at desc, eligible.video_id
                    ) as subject_rank
                  from eligible
                  join latest_batch
                    on latest_batch.subject_id=eligible.subject_id
                   and latest_batch.source_run_id=eligible.source_run_id
                )
                select * from ranked where subject_rank<=20
                order by subject_id, subject_rank limit 500
                """,
                (project, actor),
            )
            top_scores = [_json(dict(row)) for row in cur.fetchall()]
        return {
            "project_id": str(project), "subjects": subjects,
            "review_queue": review_queue, "top_scores": top_scores, "read_only": True,
        }
    except (PermissionError, ValueError):
        raise
    except Exception:
        raise RuntimeError("PROJECT_SUBJECTS_UNAVAILABLE") from None
