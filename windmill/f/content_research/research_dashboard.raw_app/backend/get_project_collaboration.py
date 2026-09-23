# py: ==3.14.*
#requirements:
#psycopg[binary]==3.3.6

"""Actor-bound project membership and accepted public evidence sharing view."""

from __future__ import annotations

import os
import re
from datetime import datetime
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
    value = os.environ.get("WM_END_USER_EMAIL", "").strip().lower()
    if not _EMAIL.fullmatch(value) or len(value) > 254:
        raise PermissionError("RESEARCH_ACTION_IDENTITY_REQUIRED")
    return value


def _project_id(value: object) -> UUID:
    if not isinstance(value, str):
        raise ValueError("project_id is invalid")
    try:
        return UUID(value)
    except ValueError:
        raise ValueError("project_id is invalid") from None


def _connect(db: postgresql):
    args: dict[str, Any] = {
        "host": db["host"], "port": int(db.get("port", 5432)),
        "user": db["user"], "password": db["password"],
        "dbname": db["dbname"], "sslmode": db.get("sslmode", "prefer"),
    }
    if db.get("options"):
        args["options"] = db["options"]
    return psycopg.connect(**args, row_factory=dict_row)


def _json(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value.isoformat() if isinstance(value, datetime) else str(value) if isinstance(value, UUID) else value
            for key, value in row.items()}


def main(db: postgresql, project_id: str):
    actor = _actor()
    project = _project_id(project_id)
    try:
        with _connect(db) as conn, conn.cursor() as cur:
            cur.execute("set transaction read only")
            cur.execute(
                """select p.id, p.name, p.organization_id,
                     (select m.role from research_project_member m
                      where m.project_id=p.id and m.actor_id=%s
                        and m.status='active' and m.effective_from <= now()
                        and (m.effective_until is null or m.effective_until > now())
                        and not exists (
                          select 1 from research_project_member newer
                          where newer.project_id=m.project_id and newer.actor_id=m.actor_id
                            and newer.effective_from <= now() and newer.effective_from > m.effective_from)
                      order by m.effective_from desc limit 1) as role
                   from research_project p
                   join research_organization o on o.id=p.organization_id
                   where p.id=%s and p.status='active' and o.status='active'
                     and project_actor_can_read(p.id,%s)""",
                (actor, project, actor),
            )
            record = cur.fetchone()
            if record is None or record["role"] is None:
                raise PermissionError("RESEARCH_PROJECT_ACCESS_DENIED")
            role = str(record["role"])
            result: dict[str, Any] = {
                "project_id": str(project), "project_name": record["name"],
                "role": role, "members": [], "outgoing": [], "incoming": [],
                "eligible_targets": [], "shared_videos": [],
            }
            if role in {"owner", "admin"}:
                cur.execute(
                    """select distinct on (actor_id)
                         actor_id, role, status, effective_from, effective_until
                       from research_project_member
                       where project_id=%s and effective_from <= now()
                       order by actor_id, effective_from desc""",
                    (project,),
                )
                result["members"] = [_json(dict(row)) for row in cur.fetchall()]
                cur.execute(
                    """select id, name from research_project
                       where organization_id=%s and id<>%s and status='active'
                       order by name, id limit 100""",
                    (record["organization_id"], project),
                )
                result["eligible_targets"] = [_json(dict(row)) for row in cur.fetchall()]
                cur.execute(
                    """select g.id, g.source_project_id, g.target_project_id,
                         p.name as other_project_name, g.status, g.effective_until,
                         g.offered_at, g.accepted_at
                       from project_video_share_grant g
                       join research_project p on p.id=g.target_project_id
                       where g.source_project_id=%s and g.status in ('offered','active')
                         and g.effective_until > now()
                       order by g.offered_at desc limit 100""",
                    (project,),
                )
                result["outgoing"] = [_json(dict(row)) for row in cur.fetchall()]
                cur.execute(
                    """select g.id, g.source_project_id, g.target_project_id,
                         p.name as other_project_name, g.status, g.effective_until,
                         g.offered_at, g.accepted_at
                       from project_video_share_grant g
                       join research_project p on p.id=g.source_project_id
                       where g.target_project_id=%s and g.status in ('offered','active')
                         and g.effective_until > now()
                       order by g.offered_at desc limit 100""",
                    (project,),
                )
                result["incoming"] = [_json(dict(row)) for row in cur.fetchall()]
            cur.execute(
                """select source_project.id as source_project_id,
                     source_project.name as source_project_name,
                     video.id as video_id, video.platform, video.title,
                     video.published_at, account.nickname as account_name,
                     metric.play_count, metric.like_count, metric.comment_count,
                     metric.captured_at as metric_captured_at
                   from project_video_share_grant grant_row
                   join research_project source_project
                     on source_project.id=grant_row.source_project_id
                   join research_project target_project
                     on target_project.id=grant_row.target_project_id
                    and target_project.organization_id=source_project.organization_id
                   join project_video_inclusion inclusion_row
                     on inclusion_row.project_id=source_project.id
                   join source_video video on video.id=inclusion_row.video_id
                   left join source_account account on account.id=video.account_id
                   left join merged_video_metric metric on metric.video_id=video.id
                   where grant_row.target_project_id=%s
                     and project_shared_video_can_read(
                       source_project.id, target_project.id, %s, video.id)
                   order by inclusion_row.last_seen_at desc, video.id
                   limit 50""",
                (project, actor),
            )
            result["shared_videos"] = [_json(dict(row)) for row in cur.fetchall()]
            return result
    except (PermissionError, ValueError):
        raise
    except Exception:
        raise RuntimeError("RESEARCH_PROJECT_COLLABORATION_UNAVAILABLE") from None
