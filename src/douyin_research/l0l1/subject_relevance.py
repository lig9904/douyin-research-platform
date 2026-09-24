"""Auditable, deterministic subject-relevance gate for project research.

Canonical public evidence remains shared in ``source_video``.  This module
only records a project's interpretation of that evidence and never deletes or
rewrites the source record.  A candidate that is not positively relevant stays
available for review, but is not passed to the subject's L1 scoring step.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb


RELEVANCE_RULE_VERSION = "subject-relevance-v1.0.0"
_DECISIONS = frozenset({"pending", "relevant", "irrelevant"})


@dataclass(frozen=True, slots=True)
class SubjectTerms:
    subject_name: str
    aliases: tuple[str, ...]
    geographic_contexts: tuple[str, ...]
    exclusions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RelevanceDecision:
    decision: str
    matched_aliases: tuple[str, ...]
    matched_geographic_contexts: tuple[str, ...]
    matched_exclusions: tuple[str, ...]


def classify(text: str | None, terms: SubjectTerms) -> RelevanceDecision:
    """Classify one candidate without a model or a provider call.

    Exclusions are decisive.  A missing positive subject match, or an alias
    match without a required geographic context, remains ``pending`` rather
    than becoming a false-negative ``irrelevant`` record.
    """
    normalized = (text or "").casefold()
    aliases = tuple(term for term in terms.aliases if term.casefold() in normalized)
    geography = tuple(
        term for term in terms.geographic_contexts if term.casefold() in normalized
    )
    exclusions = tuple(term for term in terms.exclusions if term.casefold() in normalized)
    if exclusions:
        decision = "irrelevant"
    elif aliases and (not terms.geographic_contexts or geography):
        decision = "relevant"
    else:
        decision = "pending"
    return RelevanceDecision(decision, aliases, geography, exclusions)


class SubjectRelevanceStore:
    """Persist rule decisions and manual corrections under one project scope."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def evaluate_run(
        self,
        *,
        project_id: UUID,
        subject_id: UUID,
        run_id: UUID,
        video_ids: Iterable[UUID],
        stage: str = "discovery",
    ) -> dict[UUID, str]:
        if stage not in {"discovery", "detail_preflight", "detail_enrichment"}:
            raise ValueError("subject relevance evaluation stage is invalid")
        identifiers = list(dict.fromkeys(video_ids))
        if not identifiers:
            return {}
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            terms = self._terms(cur, project_id=project_id, subject_id=subject_id)
            cur.execute(
                """
                select video.id, concat_ws(' ', video.title, video.description, account.nickname),
                       current.decision, current.decision_source
                       , current.run_id
                from source_video video
                join project_video_inclusion inclusion
                  on inclusion.video_id=video.id and inclusion.project_id=%s
                left join source_account account on account.id=video.account_id
                left join project_video_subject_relevance current
                  on current.project_id=inclusion.project_id
                 and current.video_id=inclusion.video_id and current.subject_id=%s
                where video.id=any(%s::uuid[])
                """,
                (project_id, subject_id, identifiers),
            )
            rows = cur.fetchall()
            if len(rows) != len(identifiers):
                raise ValueError("subject relevance candidates must be project-included videos")
            decisions: dict[UUID, str] = {}
            for video_id, text, prior, source, prior_run_id in rows:
                if source == "manual":
                    decisions[video_id] = str(prior)
                    continue
                result = classify(text, terms)
                match_detail = {
                    "stage": stage,
                    "aliases": list(result.matched_aliases),
                    "geographic_contexts": list(result.matched_geographic_contexts),
                    "exclusions": list(result.matched_exclusions),
                }
                cur.execute(
                    """
                    insert into project_video_subject_relevance(
                      project_id, video_id, subject_id, decision, decision_source,
                      rule_version, match_detail, run_id
                    ) values (%s,%s,%s,%s,'rule',%s,%s,%s)
                    on conflict (project_id, video_id, subject_id) do update set
                      decision=excluded.decision, decision_source='rule',
                      rule_version=excluded.rule_version, match_detail=excluded.match_detail, run_id=excluded.run_id,
                      updated_at=now()
                    where project_video_subject_relevance.decision_source='rule'
                    returning decision
                    """,
                    (
                        project_id, video_id, subject_id, result.decision,
                        RELEVANCE_RULE_VERSION, Jsonb(match_detail), run_id,
                    ),
                )
                applied = cur.fetchone()
                if applied is None:
                    # A reviewer may have changed this row to manual after our
                    # initial read.  The conditional upsert deliberately does
                    # not overwrite it; use the durable current decision for
                    # the downstream gate instead of the stale rule result.
                    cur.execute(
                        """select decision from project_video_subject_relevance
                           where project_id=%s and video_id=%s and subject_id=%s""",
                        (project_id, video_id, subject_id),
                    )
                    durable = cur.fetchone()
                    if durable is None:
                        raise RuntimeError("subject relevance write disappeared")
                    decisions[video_id] = str(durable[0])
                    cur.execute(
                        """
                        insert into project_video_subject_relevance_audit(
                          project_id, video_id, subject_id, event_type, prior_decision,
                          decision, rule_version, match_detail, run_id
                        ) values (%s,%s,%s,'rule_evaluated',%s,%s,%s,%s,%s)
                        """,
                        (
                            project_id, video_id, subject_id, prior, durable[0],
                            RELEVANCE_RULE_VERSION, Jsonb(match_detail), run_id,
                        ),
                    )
                    continue
                # An unchanged conclusion is still an event: the run ID and
                # rule version answer when this candidate was re-evaluated.
                cur.execute(
                    """
                    insert into project_video_subject_relevance_audit(
                      project_id, video_id, subject_id, event_type, prior_decision,
                      decision, rule_version, match_detail, run_id
                    ) values (%s,%s,%s,'rule_evaluated',%s,%s,%s,%s,%s)
                    """,
                    (
                        project_id, video_id, subject_id, prior, result.decision,
                        RELEVANCE_RULE_VERSION, Jsonb(match_detail), run_id,
                    ),
                )
                decisions[video_id] = result.decision
            conn.commit()
        return decisions

    def correct(
        self,
        *,
        project_id: UUID,
        subject_id: UUID,
        video_id: UUID,
        actor: str,
        decision: str,
        reason: str,
    ) -> str:
        if decision not in _DECISIONS:
            raise ValueError("subject relevance decision is invalid")
        actor = actor.strip().lower()
        reason = " ".join(reason.strip().split())
        if not actor or len(actor) > 254 or not reason or len(reason) > 500:
            raise ValueError("subject relevance correction is invalid")
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            self._terms(cur, project_id=project_id, subject_id=subject_id)
            cur.execute(
                """select decision, run_id from project_video_subject_relevance
                   where project_id=%s and video_id=%s and subject_id=%s for update""",
                (project_id, video_id, subject_id),
            )
            existing = cur.fetchone()
            if existing is None:
                raise ValueError("subject relevance must be evaluated before correction")
            prior, source_run_id = existing
            cur.execute(
                """update project_video_subject_relevance set decision=%s,
                       decision_source='manual', reviewed_by=%s, reviewed_at=now(),
                       updated_at=now() where project_id=%s and video_id=%s and subject_id=%s""",
                (decision, actor, project_id, video_id, subject_id),
            )
            cur.execute(
                """insert into project_video_subject_relevance_audit(
                     project_id, video_id, subject_id, event_type, actor, prior_decision,
                     decision, reason, rule_version, run_id
                   ) values (%s,%s,%s,'manual_override',%s,%s,%s,%s,%s,%s)""",
                (
                    project_id, video_id, subject_id, actor, prior, decision, reason,
                    RELEVANCE_RULE_VERSION, source_run_id,
                ),
            )
            conn.commit()
        return decision

    @staticmethod
    def _terms(cur, *, project_id: UUID, subject_id: UUID) -> SubjectTerms:
        cur.execute(
            """
            select subject.name, term.term, term.term_type
            from research_subject subject
            left join research_subject_term term
              on term.subject_id=subject.id and term.project_id=subject.project_id
             and term.status='active'
            where subject.id=%s and subject.project_id=%s and subject.status='active'
            order by term.term_type, term.id
            """,
            (subject_id, project_id),
        )
        rows = cur.fetchall()
        if not rows:
            raise ValueError("research subject is unavailable in this project")
        name = str(rows[0][0])
        values: dict[str, list[str]] = {"alias": [name], "geographic_context": [], "exclusion": []}
        for _, term, term_type in rows:
            if term is not None:
                values[str(term_type)].append(str(term))
        return SubjectTerms(
            subject_name=name,
            aliases=tuple(dict.fromkeys(values["alias"])),
            geographic_contexts=tuple(dict.fromkeys(values["geographic_context"])),
            exclusions=tuple(dict.fromkeys(values["exclusion"])),
        )
