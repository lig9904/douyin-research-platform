# py: ==3.14.*
#requirements:
#psycopg[binary]==3.3.6

"""Append a human Case review or read its latest project-local version."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, TypedDict
from urllib.parse import urlparse
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
_FIELDS = frozenset({"status", "source_reference", "observed_at", "video_coverage",
                     "audio_coverage", "audio_not_applicable_reason", "key_event_complete",
                     "verified_facts", "evidence_gaps", "counterevidence", "comparability_note"})
_PLATFORM_HOSTS = {
    "douyin": frozenset({"douyin.com", "www.douyin.com"}),
    "kuaishou": frozenset({"kuaishou.com", "www.kuaishou.com"}),
    "xiaohongshu": frozenset({"xiaohongshu.com", "www.xiaohongshu.com"}),
    "bilibili": frozenset({"bilibili.com", "www.bilibili.com", "m.bilibili.com"}),
    "weibo": frozenset({"weibo.com", "www.weibo.com"}),
    "wechat_channels": frozenset({"channels.weixin.qq.com"}),
}


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
    args: dict[str, Any] = {"host": db["host"], "port": int(db.get("port", 5432)),
                            "user": db["user"], "password": db["password"],
                            "dbname": db["dbname"], "sslmode": db.get("sslmode", "prefer")}
    if db.get("options"):
        args["options"] = db["options"]
    return psycopg.connect(**args, row_factory=dict_row)


def _text(payload: dict, name: str, *, maximum: int, required: bool = False) -> str:
    value = payload.get(name, "")
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise ValueError(f"{name} is invalid")
    return value.strip()


def _normalize(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != _FIELDS:
        raise ValueError("case review payload fields are invalid")
    status = payload["status"]
    video = payload["video_coverage"]
    audio = payload["audio_coverage"]
    event = payload["key_event_complete"]
    if status not in {"partial", "complete", "insufficient"}:
        raise ValueError("status is invalid")
    if video not in {"none", "partial", "complete"} or audio not in {"none", "partial", "complete", "not_applicable"}:
        raise ValueError("media coverage is invalid")
    if event is not None and type(event) is not bool:
        raise ValueError("key_event_complete is invalid")
    reference = _text(payload, "source_reference", maximum=512, required=True)
    parsed = urlparse(reference)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.port or parsed.params or parsed.query or parsed.fragment or not parsed.path or parsed.path == "/"):
        raise ValueError("source_reference must be a clean HTTPS original-post URL")
    observed = payload["observed_at"]
    if not isinstance(observed, str):
        raise ValueError("observed_at is invalid")
    try:
        observed_at = datetime.fromisoformat(observed.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("observed_at is invalid") from None
    if observed_at.tzinfo is None or observed_at.utcoffset() is None or observed_at > datetime.now(timezone.utc):
        raise ValueError("observed_at must be a past timezone-aware time")
    reason = payload["audio_not_applicable_reason"]
    if reason is not None and (not isinstance(reason, str) or not reason.strip() or len(reason) > 1000):
        raise ValueError("audio_not_applicable_reason is invalid")
    reason = reason.strip() if reason else None
    if (audio == "not_applicable") != (reason is not None):
        raise ValueError("audio_not_applicable_reason must match audio_coverage")
    facts = _text(payload, "verified_facts", maximum=4000, required=status == "complete")
    gaps = _text(payload, "evidence_gaps", maximum=4000)
    counter = _text(payload, "counterevidence", maximum=4000)
    comparison = _text(payload, "comparability_note", maximum=4000)
    if event is False and status != "insufficient":
        raise ValueError("missing key event requires insufficient")
    if status == "complete":
        if video != "complete" or audio not in {"complete", "not_applicable"} or event is not True or not comparison:
            raise ValueError("complete requires full media, key event, facts and comparability")
        if audio == "not_applicable":
            rationale = (reason or "").lower()
            source_silent = any(term in rationale for term in ("无声", "无音频", "没有音频", "静音", "silent", "no audio", "muted"))
            question_visual = any(term in rationale for term in ("不依赖", "不涉及", "不影响", "无关", "画面", "视觉", "visual"))
            if (len(rationale) < 12 or not source_silent or not question_visual or
                    any(term in rationale for term in ("没听", "未听", "听不到", "无法听", "未观看", "没看", "听不清"))):
                raise ValueError("complete requires a specific no-audio applicability reason")
    return {"status": status, "source_reference": reference,
            "observed_at": observed_at.astimezone(timezone.utc).isoformat(),
            "video_coverage": video, "audio_coverage": audio, "audio_not_applicable_reason": reason,
            "key_event_complete": event, "verified_facts": facts, "evidence_gaps": gaps,
            "counterevidence": counter, "comparability_note": comparison}


def _source_reference_matches(reference: str, source: dict[str, Any]) -> bool:
    parsed = urlparse(reference)
    platform = source["platform"]
    if parsed.hostname not in _PLATFORM_HOSTS.get(platform, frozenset()):
        return False
    video_key = str(source["platform_video_id"])
    if platform == "douyin":
        return re.fullmatch(rf"/video/{re.escape(video_key)}/?", parsed.path) is not None
    # Match known original-post path shapes, never an arbitrary profile/search
    # path that happens to contain the identifier.
    direct_paths = {
        "xiaohongshu": (rf"/explore/{re.escape(video_key)}/?", rf"/discovery/item/{re.escape(video_key)}/?"),
        "kuaishou": (rf"/short-video/{re.escape(video_key)}/?",),
        "bilibili": (rf"/video/{re.escape(video_key)}/?",),
        "weibo": (rf"/status/{re.escape(video_key)}/?",),
    }
    if any(re.fullmatch(pattern, parsed.path) for pattern in direct_paths.get(platform, ())):
        return True
    # source_url is untrusted upstream metadata: even an exact match may be a
    # profile or search page rather than the original post. Unknown URL shapes
    # require an explicit parser/ID-binding rule before they can unlock a case.
    return False


def _json(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: (value.isoformat() if isinstance(value, datetime) else str(value) if isinstance(value, UUID) else value)
            for key, value in row.items()}


def main(db: postgresql, project_id: str, video_id: str, action: str,
         payload: dict | None = None, idempotency_key: str | None = None):
    actor = _actor()
    project = _uuid(project_id, "project_id")
    video = _uuid(video_id, "video_id")
    if action not in {"create", "latest"}:
        raise ValueError("action is invalid")
    values = _normalize(payload) if action == "create" else None
    key = _uuid(idempotency_key, "idempotency_key") if action == "create" else None
    try:
        with _connect(db) as conn, conn.cursor() as cur:
            if action == "latest":
                cur.execute("set transaction isolation level repeatable read read only")
            cur.execute(
                """select p.id from research_project p
                   join research_organization o on o.id=p.organization_id
                   where p.id=%s and p.status='active' and o.status='active'
                     and project_actor_can_read(p.id,%s)
                     and exists (
                       select 1 from research_project_member m
                       where m.project_id=p.id and m.actor_id=%s
                         and (%s='latest' or m.role in ('owner','admin','researcher'))
                         and m.status='active'
                         and m.effective_from<=now()
                         and (m.effective_until is null or m.effective_until>now())
                         and not exists (
                           select 1 from research_project_member newer
                           where newer.project_id=m.project_id and newer.actor_id=m.actor_id
                             and newer.effective_from<=now()
                             and newer.effective_from>m.effective_from))"""
                + (" for update of p" if action == "create" else ""),
                (project, actor, actor, action),
            )
            if cur.fetchone() is None:
                raise PermissionError("RESEARCH_PROJECT_CASE_REVIEW_DENIED")
            if action == "latest":
                cur.execute("""select * from project_video_case_review
                               where project_id=%s and video_id=%s
                               order by version_no desc limit 1""", (project, video))
                return {"review": _json(cur.fetchone())}
            assert values is not None and key is not None
            cur.execute("select platform,platform_video_id,source_url from source_video where id=%s", (video,))
            source = cur.fetchone()
            if source is None:
                raise ValueError("video is not found")
            digest = hashlib.sha256(json.dumps(
                {"project_id": str(project), "video_id": str(video), "payload": values},
                sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            ).encode()).hexdigest()
            cur.execute("""insert into research_user_action(idempotency_key,actor,action_type,payload_hash)
                           values (%s,%s,'project_case_review.create',%s)
                           on conflict do nothing returning idempotency_key""", (key, actor, digest))
            if cur.fetchone() is None:
                cur.execute("""select actor,action_type,payload_hash,outcome from research_user_action
                               where idempotency_key=%s""", (key,))
                replay = cur.fetchone()
                if (not replay or replay["actor"] != actor or replay["action_type"] != "project_case_review.create"
                        or replay["payload_hash"] != digest or not isinstance(replay["outcome"], dict)):
                    raise ValueError("RESEARCH_ACTION_IDEMPOTENCY_CONFLICT")
                return {**replay["outcome"], "idempotent_replay": True}
            if not _source_reference_matches(values["source_reference"], source):
                raise ValueError("source_reference does not match video_id")
            cur.execute("""select 1 from project_video_inclusion
                           where project_id=%s and video_id=%s and status='accepted'""", (project, video))
            if cur.fetchone() is None:
                raise ValueError("locally accepted project video required")
            cur.execute("""select coalesce(max(version_no),0)+1 as next_version
                           from project_video_case_review where project_id=%s and video_id=%s""", (project, video))
            version = cur.fetchone()["next_version"]
            cur.execute("""insert into project_video_case_review
                           (project_id,video_id,version_no,status,source_reference,observed_at,
                            video_coverage,audio_coverage,audio_not_applicable_reason,key_event_complete,
                            verified_facts,evidence_gaps,counterevidence,comparability_note,reviewed_by)
                           values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
                        (project, video, version, values["status"], values["source_reference"], values["observed_at"],
                         values["video_coverage"], values["audio_coverage"], values["audio_not_applicable_reason"],
                         values["key_event_complete"], values["verified_facts"], values["evidence_gaps"],
                         values["counterevidence"], values["comparability_note"], actor))
            result = {"review_id": str(cur.fetchone()["id"]), "project_id": str(project), "video_id": str(video),
                      "version_no": version, "status": values["status"], "idempotent_replay": False}
            cur.execute("update research_user_action set outcome=%s where idempotency_key=%s", (Jsonb(result), key))
            return result
    except (PermissionError, ValueError):
        raise
    except Exception:
        raise RuntimeError("RESEARCH_PROJECT_CASE_REVIEW_UNAVAILABLE") from None
