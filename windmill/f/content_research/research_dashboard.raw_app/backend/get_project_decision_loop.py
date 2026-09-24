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
                          c.created_at, c.updated_at,
                          case when binding.decision_card_id is null then null else jsonb_build_object(
                            'profile_id', binding.profile_id,
                            'profile_kind', binding.profile_kind,
                            'profile_version_no', binding.profile_version_no,
                            'rights_status_at_binding', binding.rights_status_at_binding,
                            'profile_current_status', bound_profile.status
                          ) end as profile_binding
                   from project_decision_card c
                   left join project_decision_card_profile_binding binding
                     on binding.decision_card_id=c.id and binding.project_id=c.project_id
                   left join research_subject_profile_version bound_profile
                     on bound_profile.id=binding.profile_id and bound_profile.project_id=c.project_id
                   left join project_video_inclusion i
                     on i.project_id=c.project_id and i.video_id=c.source_video_id
                   join source_video v on v.id=c.source_video_id
                   left join source_account a on a.id=v.account_id
                   left join research_subject subject_row
                     on subject_row.id=c.subject_id and subject_row.project_id=c.project_id
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
                """select p.id, p.decision_card_id, p.publication_date, p.title,
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
                "card_source": "仅本项目已接受的公开视频；项目间共享依据不可建行动卡。",
                "outcomes": "仅按日非个人汇总指标；不保存观众、评论原文或账号凭据。",
            },
        }
    except (PermissionError, ValueError):
        raise
    except Exception:
        raise RuntimeError("RESEARCH_PROJECT_DECISION_LOOP_UNAVAILABLE") from None
