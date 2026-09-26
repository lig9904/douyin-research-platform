# py: ==3.14.*
#requirements:
#psycopg[binary]==3.3.6

"""Preview and explicitly accept one already-known public Douyin video.

This route never calls a provider or searches the global library. A manager
must provide an exact public video ID or direct URL; project membership is
checked again on the server for both preview and mutation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, TypedDict
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


class postgresql(TypedDict):
    host: str
    port: int
    user: str
    password: str
    dbname: str
    sslmode: str


_EMAIL = re.compile(r"^[^\s@]{1,128}@[^\s@]{1,120}$")
_VIDEO_ID = re.compile(r"^[0-9]{8,32}$")


def _actor() -> str:
    value = os.environ.get("WM_END_USER_EMAIL", "").strip().lower()
    if len(value) > 254 or not _EMAIL.fullmatch(value):
        raise PermissionError("RESEARCH_ACTION_IDENTITY_REQUIRED")
    return value


def _uuid(value: object, name: str) -> UUID:
    if not isinstance(value, str):
        raise ValueError(f"{name} is invalid")
    try:
        return UUID(value)
    except ValueError:
        raise ValueError(f"{name} is invalid") from None


def _video_key(value: object) -> str:
    if not isinstance(value, str) or len(value) > 512:
        raise ValueError("video reference is invalid")
    raw = value.strip()
    if _VIDEO_ID.fullmatch(raw):
        return raw
    parsed = urlparse(raw)
    if parsed.scheme != "https" or parsed.hostname not in {"douyin.com", "www.douyin.com"}:
        raise ValueError("use a Douyin video ID or direct HTTPS video URL")
    if parsed.username or parsed.password or parsed.port or parsed.fragment:
        raise ValueError("video reference is invalid")
    query = parse_qs(parsed.query, keep_blank_values=True)
    path = re.fullmatch(r"/video/([0-9]{8,32})/?", parsed.path)
    if path:
        modal_ids = query.get("modal_id", [])
        if modal_ids and modal_ids != [path.group(1)]:
            raise ValueError("video reference has conflicting IDs")
        return path.group(1)
    if parsed.path == "/" or re.fullmatch(r"/user/[^/]{1,128}/?", parsed.path):
        modal_ids = query.get("modal_id", [])
        if len(modal_ids) == 1 and _VIDEO_ID.fullmatch(modal_ids[0]):
            return modal_ids[0]
    raise ValueError("use a Douyin video ID or direct HTTPS video URL")


def _connect(db: postgresql):
    args: dict[str, Any] = {
        "host": db["host"], "port": int(db.get("port", 5432)),
        "user": db["user"], "password": db["password"],
        "dbname": db["dbname"], "sslmode": db.get("sslmode", "prefer"),
    }
    if db.get("options"):
        args["options"] = db["options"]
    return psycopg.connect(**args, row_factory=dict_row)


def _manager(cur, project_id: UUID, actor: str, *, lock: bool) -> None:
    cur.execute(
        """select p.id from research_project p
           join research_organization o on o.id=p.organization_id
           where p.id=%s and p.status='active' and o.status='active'
             and project_actor_can_read(p.id,%s)
             and exists (
               select 1 from research_project_member m
               where m.project_id=p.id and m.actor_id=%s
                 and m.role in ('owner','admin') and m.status='active'
                 and m.effective_from <= now()
                 and (m.effective_until is null or m.effective_until > now())
                 and not exists (
                   select 1 from research_project_member newer
                   where newer.project_id=m.project_id and newer.actor_id=m.actor_id
                     and newer.effective_from <= now()
                     and newer.effective_from > m.effective_from)
             )""" + (" for update of p" if lock else ""),
        (project_id, actor, actor),
    )
    if cur.fetchone() is None:
        raise PermissionError("RESEARCH_PROJECT_MANAGE_DENIED")


def _candidate(cur, project_id: UUID, video_key: str) -> dict[str, Any] | None:
    cur.execute(
        """select v.id, v.platform, v.platform_video_id, v.title,
             v.published_at, a.nickname as account_name,
             i.status as project_status, i.source_type as project_source_type,
             metric.play_count, metric.like_count, metric.comment_count,
             metric.captured_at as metric_captured_at
           from source_video v
           left join source_account a on a.id=v.account_id
           left join project_video_inclusion i
             on i.project_id=%s and i.video_id=v.id
           left join merged_video_metric metric on metric.video_id=v.id
           where v.platform='douyin' and v.platform_video_id=%s
             and v.availability_status='available'""",
        (project_id, video_key),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {key: value.isoformat() if hasattr(value, "isoformat") else str(value) if isinstance(value, UUID) else value
            for key, value in row.items()}


def main(db: postgresql, project_id: str, action: str,
         video_reference: str, idempotency_key: str | None = None,
         content_review_confirmed: bool = False):
    actor = _actor()
    project = _uuid(project_id, "project_id")
    if action not in {"preview", "accept"}:
        raise ValueError("action is invalid")
    if action == "accept" and content_review_confirmed is not True:
        raise ValueError("video content review confirmation is required")
    video_key = _video_key(video_reference)
    key = _uuid(idempotency_key, "idempotency_key") if action == "accept" else None
    try:
        with _connect(db) as conn, conn.cursor() as cur:
            if action == "preview":
                cur.execute("set transaction read only")
            _manager(cur, project, actor, lock=action == "accept")
            candidate = _candidate(cur, project, video_key)
            if action == "preview":
                return {"found": candidate is not None, "video": candidate}
            if candidate is None:
                raise ValueError("video is not in the existing public library")
            digest = hashlib.sha256(json.dumps(
                {"project_id": str(project), "video_id": candidate["id"], "action": action},
                sort_keys=True, separators=(",", ":"),
            ).encode()).hexdigest()
            cur.execute(
                """insert into research_user_action(idempotency_key,actor,action_type,payload_hash)
                   values (%s,%s,'project_video.accept',%s)
                   on conflict do nothing returning idempotency_key""",
                (key, actor, digest),
            )
            if cur.fetchone() is None:
                cur.execute(
                    """select actor, action_type, payload_hash, outcome
                       from research_user_action where idempotency_key=%s""",
                    (key,),
                )
                replay = cur.fetchone()
                if not replay or replay["actor"] != actor or replay["action_type"] != "project_video.accept" or replay["payload_hash"] != digest or not isinstance(replay["outcome"], dict):
                    raise ValueError("RESEARCH_ACTION_IDEMPOTENCY_CONFLICT")
                return {**replay["outcome"], "idempotent_replay": True}
            cur.execute(
                """insert into project_video_inclusion
                   (project_id,video_id,source_type,source_ref,status,metadata)
                   values (%s,%s,'manual',%s,'accepted',
                     jsonb_build_object('last_accepted_by',%s::text,'last_accepted_at',now(),
                       'content_review_confirmed_by',%s::text,'content_review_confirmed_at',now()))
                   on conflict (project_id,video_id) do update
                     set status='accepted', updated_at=now(),
                         metadata=project_video_inclusion.metadata ||
                           jsonb_build_object('last_accepted_by',%s::text,'last_accepted_at',now(),
                             'content_review_confirmed_by',%s::text,'content_review_confirmed_at',now())
                   where project_video_inclusion.status <> 'accepted'
                   returning video_id""",
                (project, candidate["id"], f"douyin:{video_key}", actor, actor, actor, actor),
            )
            changed = cur.fetchone() is not None
            outcome = {"changed": changed, "project_id": str(project), "video_id": candidate["id"], "status": "accepted"}
            cur.execute(
                "update research_user_action set outcome=%s where idempotency_key=%s",
                (Jsonb(outcome), key),
            )
            return outcome
    except (PermissionError, ValueError):
        raise
    except Exception:
        raise RuntimeError("RESEARCH_PROJECT_VIDEO_EVIDENCE_UNAVAILABLE") from None
