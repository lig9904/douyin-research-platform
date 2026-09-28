# py: ==3.14.*
#requirements:
#psycopg[binary]==3.3.6

"""Read the project-local decision loop without exposing shared/raw evidence."""

from __future__ import annotations

import os
import re
from datetime import date, datetime
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
    if len(actor) > 254 or not _EMAIL.fullmatch(actor):
        raise PermissionError("RESEARCH_ACTION_IDENTITY_REQUIRED")
    return actor


def _uuid(value: object, name: str) -> UUID:
    if not isinstance(value, str):
        raise ValueError(f"{name} is invalid")
    try:
        return UUID(value)
    except ValueError:
        raise ValueError(f"{name} is invalid") from None


def _connect(db: postgresql):
    args: dict[str, Any] = {
        "host": db["host"], "port": int(db.get("port", 5432)),
        "user": db["user"], "password": db["password"],
        "dbname": db["dbname"], "sslmode": db.get("sslmode", "prefer"),
    }
    if db.get("options"):
        args["options"] = db["options"]
    return psycopg.connect(**args, row_factory=dict_row)


def _json(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json(item) for item in value]
    return value


def main(db: postgresql, project_id: str):
    actor = _actor()
    project = _uuid(project_id, "project_id")
    try:
        with _connect(db) as conn, conn.cursor() as cur:
            # One consistent ACL/data snapshot: an intervening revocation cannot
            # mix a pre-revocation permission decision with later rows.
            cur.execute("set transaction isolation level repeatable read read only")
            cur.execute(
                """select p.name, member_row.role
                   from research_project p
                   join research_organization o on o.id=p.organization_id
                   join lateral (
                     select m.role, m.status, m.effective_until
                     from research_project_member m
                     where m.project_id=p.id and m.actor_id=%s and m.effective_from<=now()
                     order by m.effective_from desc limit 1
                   ) member_row on true
                   where p.id=%s and p.status='active' and o.status='active'
                     and member_row.status='active'
                     and (member_row.effective_until is null or member_row.effective_until>now())""",
                (actor, project),
            )
            access = cur.fetchone()
            if access is None:
                raise PermissionError("RESEARCH_PROJECT_ACCESS_DENIED")
            cur.execute(
                """select c.id, c.source_video_id, c.subject_id, subject_row.name as subject_name,
                          v.platform, v.platform_video_id,
                          v.title as source_video_title, a.nickname as source_account_name,
                          c.hypothesis, c.reference_point, c.adaptation_difference,
                          c.evaluation_metric, c.success_rule, c.observation_window_days,
                          c.comparison_basis, c.confounder_plan,
                          c.owner_actor, c.decision, c.status, c.review_verdict, c.review_conclusion,
                          c.review_evidence, c.next_action, c.reviewed_by, c.reviewed_at, c.created_by,
                          c.review_observation_id, c.review_observation_version, c.review_metric_snapshot,
                          (i.project_id is null or i.status <> 'accepted' or v.availability_status <> 'available') as source_reference_withdrawn,
                          source_case_review.status as source_case_review_status,
                          (source_case_review.id = c.source_case_review_id
                           and source_case_review.status = 'complete') as source_case_review_is_current,
                          bound_source_review.version_no as source_case_review_version_at_binding,
                          bound_source_review.source_reference as source_case_review_reference_at_binding,
                          bound_source_review.verified_facts as source_case_review_facts_at_binding,
                          bound_source_review.evidence_gaps as source_case_review_gaps_at_binding,
                          bound_source_review.counterevidence as source_case_review_counterevidence_at_binding,
                          bound_source_review.comparability_note as source_case_review_comparability_at_binding,
                          c.created_at, c.updated_at,
                          case when binding.decision_card_id is null then null else jsonb_build_object(
                            'profile_id', binding.profile_id,
                            'profile_kind', binding.profile_kind,
                            'profile_version_no', binding.profile_version_no,
                            'rights_status_at_binding', binding.rights_status_at_binding,
                            'profile_current_status', bound_profile.status,
                            'profile_current_rights_status', bound_profile.rights_status
                          ) end as profile_binding,
                          coalesce(evidence_ref_rows.refs, '[]'::jsonb) as evidence_refs
                   from project_decision_card c
                   left join project_decision_card_profile_binding binding
                     on binding.decision_card_id=c.id and binding.project_id=c.project_id
                   left join research_subject_profile_version bound_profile
                     on bound_profile.id=binding.profile_id and bound_profile.project_id=c.project_id
                   left join project_video_inclusion i
                     on i.project_id=c.project_id and i.video_id=c.source_video_id
                   left join lateral (
                     select review.id, review.status from project_video_case_review review
                     where review.project_id=c.project_id and review.video_id=c.source_video_id
                     order by review.version_no desc limit 1
                   ) source_case_review on true
                   left join project_video_case_review bound_source_review
                     on bound_source_review.id=c.source_case_review_id
                    and bound_source_review.project_id=c.project_id
                    and bound_source_review.video_id=c.source_video_id
                   join source_video v on v.id=c.source_video_id
                   left join source_account a on a.id=v.account_id
                   left join research_subject subject_row
                     on subject_row.id=c.subject_id and subject_row.project_id=c.project_id
                   left join lateral (
                     select jsonb_agg(jsonb_build_object(
                       'id', ref.id, 'video_id', ref.video_id, 'role', ref.role,
                       'reason', ref.reason, 'video_title', ref.video_title_at_binding,
                       'account_name', ref.account_name_at_binding,
                       'case_review_status', ref_case_review.status,
                       'case_review_is_current',
                         (ref_case_review.id = ref.case_review_id_at_binding
                          and ref_case_review.status = 'complete'),
                       'case_review_version_at_binding', bound_ref_review.version_no,
                       'case_review_reference_at_binding', bound_ref_review.source_reference,
                       'case_review_facts_at_binding', bound_ref_review.verified_facts,
                       'case_review_gaps_at_binding', bound_ref_review.evidence_gaps,
                       'case_review_counterevidence_at_binding', bound_ref_review.counterevidence,
                       'case_review_comparability_at_binding', bound_ref_review.comparability_note,
                       'reference_withdrawn',
                         (ref_inclusion.project_id is null or ref_inclusion.status <> 'accepted'
                          or ref_video.availability_status <> 'available')
                     ) order by ref.position) as refs
                     from project_decision_card_evidence_ref ref
                     join source_video ref_video on ref_video.id=ref.video_id
                     left join project_video_inclusion ref_inclusion
                       on ref_inclusion.project_id=ref.project_id and ref_inclusion.video_id=ref.video_id
                     left join lateral (
                       select review.id, review.status from project_video_case_review review
                       where review.project_id=ref.project_id and review.video_id=ref.video_id
                       order by review.version_no desc limit 1
                     ) ref_case_review on true
                     left join project_video_case_review bound_ref_review
                       on bound_ref_review.id=ref.case_review_id_at_binding
                      and bound_ref_review.project_id=ref.project_id
                      and bound_ref_review.video_id=ref.video_id
                     where ref.project_id=c.project_id and ref.decision_card_id=c.id
                   ) evidence_ref_rows on true
                   where c.project_id=%s
                   order by c.updated_at desc, c.id
                   limit 200""",
                (project,),
            )
            cards = [_json(dict(row)) for row in cur.fetchall()]
            card_ids = [UUID(item["id"]) for item in cards]
            events: list[dict[str, Any]] = []
            if card_ids:
                cur.execute(
                    """select decision_card_id, actor_id, action, from_status, to_status,
                              changed_fields, created_at
                       from project_decision_card_event
                       where project_id=%s and decision_card_id=any(%s)
                       order by created_at desc, id desc
                       limit 500""",
                    (project, card_ids),
                )
                events = [_json(dict(row)) for row in cur.fetchall()]
            cur.execute(
                """select v.id, v.platform, v.platform_video_id, v.title,
                          a.nickname as account_name
                   from project_video_inclusion i
                   join source_video v on v.id=i.video_id and v.availability_status='available'
                   left join source_account a on a.id=v.account_id
                   join lateral (
                     select review.status from project_video_case_review review
                     where review.project_id=i.project_id and review.video_id=i.video_id
                     order by review.version_no desc limit 1
                   ) case_review on case_review.status='complete'
                   where i.project_id=%s and i.status='accepted'
                   order by i.updated_at desc, v.id
                   limit 200""",
                (project,),
            )
            eligible_videos = [_json(dict(row)) for row in cur.fetchall()]
            cur.execute(
                """select id, name, subject_type from research_subject
                   where project_id=%s and status='active'
                   order by subject_type, lower(name), id limit 200""",
                (project,),
            )
            subjects = [_json(dict(row)) for row in cur.fetchall()]
            cur.execute(
                """select id, subject_id, profile_kind, version_no, summary, rights_status
                   from research_subject_profile_version
                   where project_id=%s and status='approved'
                   order by subject_id, profile_kind, version_no desc, id
                   limit 200""",
                (project,),
            )
            approved_profiles = [_json(dict(row)) for row in cur.fetchall()]
            cur.execute(
                """select p.id, p.decision_card_id, p.publication_date, p.published_at, p.title,
                          p.platform, p.account_reference, p.platform_content_id,
                          p.content_version, p.distribution_mode,
                          p.content_reference, p.status, p.created_by, p.created_at, p.updated_at,
                          coalesce(jsonb_agg(jsonb_build_object(
                            'id', m.id, 'metric_date', m.metric_date, 'version', m.version, 'impressions', m.impressions,
                            'engagements', m.engagements, 'likes', m.likes, 'comments', m.comments,
                            'shares', m.shares, 'follows', m.follows, 'conversions', m.conversions,
                            'source', m.source, 'source_reference', m.source_reference,
                            'source_reported_at', m.source_reported_at,
                            'source_version_or_digest', m.source_version_or_digest,
                            'measurement_scope', m.measurement_scope, 'recorded_at', m.recorded_at
                          ) order by m.metric_date desc, m.version desc) filter (where m.publication_id is not null), '[]'::jsonb) as daily_observations
                   from project_publication_record p
                   left join project_publication_metric_observation m
                     on m.publication_id=p.id and m.project_id=p.project_id
                   where p.project_id=%s and p.status='published'
                   group by p.id
                   order by p.publication_date desc, p.created_at desc
                   limit 200""",
                (project,),
            )
            publications = [_json(dict(row)) for row in cur.fetchall()]
        return {
            "project_id": str(project), "project_name": access["name"], "role": access["role"],
            "cards": cards, "events": events, "eligible_videos": eligible_videos, "subjects": subjects,
            "approved_profiles": approved_profiles, "publications": publications,
            "boundaries": {
                "card_source": "仅本项目已纳入且最新案例核看完整的公开视频可新建行动卡；项目间共享依据不可建卡。历史卡保留，但须标明当前核看状态。",
                "outcomes": "仅按日非个人汇总指标；不保存观众、评论原文或账号凭据。",
            },
        }
    except (PermissionError, ValueError):
        raise
    except Exception:
        raise RuntimeError("RESEARCH_PROJECT_DECISION_LOOP_UNAVAILABLE") from None
