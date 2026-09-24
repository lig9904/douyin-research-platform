"""Project-private ASR review and dispatch boundary.

This module intentionally does not read a storage secret, create a presigned
URL, or use canonical ASR tables.  A trusted worker supplies a narrow
``provider_factory`` only after this service has locked and rechecked the
project-local approval immediately before its HTTP-capable provider is made.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Callable, Mapping, Protocol
from uuid import UUID
from urllib.parse import urlsplit

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from douyin_research.l2.asr_execution import ASRProvider, ASRProviderRequest
from douyin_research.l2.transcripts import TaskCost, TranscriptEvidence
from douyin_research.media_assets import MediaAssetReference
from douyin_research.l2.media_review import _origin, asset_fingerprint


GLOBAL_REVIEWERS_PATH = "f/content_research/project_asr_global_reviewers"
IDENTITY_SOURCE = "windmill_end_user_email_allowlist_v1"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ACTOR = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+\Z")


class ProjectASRProvider(ASRProvider, Protocol):
    """Provider receives only a delivery URL from a trusted worker factory."""


@dataclass(frozen=True, slots=True)
class ProjectMediaReviewInput:
    project_id: UUID | str
    video_id: UUID | str
    asset_id: UUID | str
    review_version: str
    review_statement: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ProjectASRDispatch:
    project_id: UUID | str
    video_id: UUID | str
    media_review_id: UUID | str
    provider: str
    model_id: str
    model_revision: str
    engine_version: str
    source_fingerprint: str
    cost_currency: str = "CNY"


class ProjectASRService:
    """Persist and execute only project-local ASR work.

    The process identity is taken exclusively from Windmill's authenticated
    end-user environment.  It is never accepted from a JSON request.  Global
    reviewers may approve/revoke any project; project owners/admins can do so
    only for their own project; active members may preview.
    """

    def __init__(self, dsn: str, *, delivery_origin: str,
                 variable: Callable[[str], str] | None = None) -> None:
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("dsn is required")
        self._dsn = dsn
        self._delivery_origin = _origin(delivery_origin)
        self._variable = variable or _windmill_variable

    def preview(self, request: ProjectMediaReviewInput) -> dict[str, object]:
        actor = self._actor()
        project_id, video_id, asset_id = _ids(request)
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            self._assert_member(conn, project_id, actor)
            self._assert_dispatchable_video(conn, project_id, video_id)
            asset = self._asset(conn, video_id, asset_id)
            manifest = asset_manifest(asset, self._delivery_origin)
            row = conn.execute(
                """select id,status,asset_manifest_fingerprint from project_asr_media_review
                   where project_id=%s and video_id=%s and asset_id=%s and review_version=%s""",
                (project_id, video_id, asset_id, request.review_version),
            ).fetchone()
        status = "not_reviewed" if row is None else (
            "stale" if row["asset_manifest_fingerprint"] != manifest else row["status"]
        )
        return {"project_id": str(project_id), "video_id": str(video_id), "asset_id": str(asset_id),
                "review_version": request.review_version, "asset_manifest_fingerprint": manifest,
                "media_fingerprint": asset.content_sha256, "review_status": status,
                "db_writes": 0, "external_calls": 0, "storage_credentials_exposed": False}

    def approve(self, request: ProjectMediaReviewInput, *, expected_manifest_fingerprint: str) -> dict[str, object]:
        actor = self._actor()
        project_id, video_id, asset_id = _ids(request)
        _review_input(request, expected_manifest_fingerprint)
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            self._assert_reviewer(conn, project_id, actor)
            self._assert_dispatchable_video(conn, project_id, video_id)
            asset = self._asset(conn, video_id, asset_id)
            manifest = asset_manifest(asset, self._delivery_origin)
            if manifest != expected_manifest_fingerprint:
                raise ValueError("project media changed since preview")
            row = conn.execute(
                """insert into project_asr_media_review(
                     project_id,video_id,asset_id,review_version,media_fingerprint,
                     asset_manifest_fingerprint,delivery_origin,identity_source,review_statement)
                   values(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   on conflict(project_id,asset_id,review_version) do nothing returning id""",
                (project_id, video_id, asset_id, request.review_version, asset.content_sha256,
                 manifest, self._delivery_origin, IDENTITY_SOURCE, Jsonb(dict(request.review_statement))),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    """select id,status,asset_manifest_fingerprint,media_fingerprint from project_asr_media_review
                       where project_id=%s and asset_id=%s and review_version=%s for update""",
                    (project_id, asset_id, request.review_version),
                ).fetchone()
                if row["asset_manifest_fingerprint"] != manifest or row["media_fingerprint"] != asset.content_sha256:
                    raise ValueError("project media review conflicts with prior immutable review")
                if row["status"] == "revoked":
                    raise ValueError("revoked project media review requires a new review version")
            review_id = row["id"]
            conn.execute(
                """update project_asr_media_review set status='approved', reviewed_by=%s
                   where id=%s and project_id=%s and status='draft'""",
                (actor, review_id, project_id),
            )
            current = conn.execute("select status from project_asr_media_review where id=%s", (review_id,)).fetchone()
            if current is None or current["status"] != "approved":
                raise ValueError("project media review is not approvable")
        return {"status": "approved", "review_id": str(review_id), "project_id": str(project_id),
                "asset_manifest_fingerprint": manifest, "storage_credentials_exposed": False}

    def revoke(self, *, project_id: UUID | str, media_review_id: UUID | str) -> dict[str, object]:
        actor = self._actor()
        project_id, review_id = _uuid(project_id, "project_id"), _uuid(media_review_id, "media_review_id")
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            self._assert_reviewer(conn, project_id, actor)
            review_scope = conn.execute(
                "select video_id from project_asr_media_review where id=%s and project_id=%s",
                (review_id, project_id),
            ).fetchone()
            if review_scope is None:
                raise ValueError("project media review is unavailable or already revoked")
            self._assert_dispatchable_video(conn, project_id, review_scope["video_id"])
            row = conn.execute(
                """update project_asr_media_review set status='revoked', revoked_by=%s
                   where id=%s and project_id=%s and status in ('draft','approved') returning id""",
                (actor, review_id, project_id),
            ).fetchone()
            if row is None:
                raise ValueError("project media review is unavailable or already revoked")
        return {"status": "revoked", "review_id": str(review_id), "project_id": str(project_id)}

    def dispatch(
        self,
        request: ProjectASRDispatch,
        *,
        media_url_factory: Callable[[MediaAssetReference], str],
        provider_factory: Callable[[], ProjectASRProvider],
    ) -> dict[str, object]:
        """Submit once after the final locked review recheck; no global reuse."""
        actor = self._actor()
        project_id, video_id, review_id = (_uuid(request.project_id, "project_id"),
                                           _uuid(request.video_id, "video_id"),
                                           _uuid(request.media_review_id, "media_review_id"))
        _dispatch_input(request)
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            self._assert_reviewer(conn, project_id, actor)
            self._assert_dispatchable_video(conn, project_id, video_id)
            review = conn.execute(
                """select * from project_asr_media_review where id=%s and project_id=%s and video_id=%s
                   for update""", (review_id, project_id, video_id),
            ).fetchone()
            if review is None or review["status"] != "approved":
                raise PermissionError("current approved project media review is required before ASR HTTP")
            asset = self._asset(conn, video_id, review["asset_id"])
            manifest = asset_manifest(asset, self._delivery_origin)
            if review["asset_manifest_fingerprint"] != manifest or review["media_fingerprint"] != asset.content_sha256:
                raise PermissionError("approved project media review is stale")
            task_key = _task_key(project_id, video_id, review_id, review["asset_id"], review["review_version"],
                                 asset.content_sha256, request.model_id, request.model_revision, request.engine_version)
            existing = conn.execute("select status from project_asr_execution_job where task_key=%s", (task_key,)).fetchone()
            if existing is not None:
                return {"status": existing["status"], "task_key": task_key, "created": False,
                        "external_calls": 0, "project_private": True}
            job = conn.execute(
                """insert into project_asr_execution_job(task_key,project_id,video_id,media_review_id,reviewed_asset_id,
                     review_version,media_fingerprint,asset_manifest_fingerprint,provider,model_id,model_revision,
                     engine_version,source_fingerprint,status,cost_currency)
                   values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'submitting',%s) returning id""",
                (task_key, project_id, video_id, review_id, asset.id, review["review_version"], asset.content_sha256,
                 manifest, request.provider, request.model_id, request.model_revision, request.engine_version,
                 request.source_fingerprint, request.cost_currency),
            ).fetchone()
        # URL creation and provider construction happen only after approval
        # record was locked/rechecked; they are never persisted or returned.
        try:
            media_url = media_url_factory(asset)
            self._assert_delivery_url(media_url)
            provider = provider_factory()
            state = provider.submit(ASRProviderRequest(task_key=task_key, media_ref=media_url,
                source_fingerprint=request.source_fingerprint, model_id=request.model_id,
                model_revision=request.model_revision, engine_version=request.engine_version))
            calls = 1
            self._record_state(project_id, video_id, review_id, UUID(str(job["id"])), state, request.cost_currency)
            # Bounded polling keeps the project-local transcript path usable;
            # every result is persisted only in project_* tables.
            for _ in range(3):
                if state.status not in {"submitted", "running"}:
                    break
                state = provider.poll(state.provider_task_ref)
                calls += 1
                self._record_state(project_id, video_id, review_id, UUID(str(job["id"])), state, request.cost_currency)
        except Exception:
            self._mark_failed(UUID(str(job["id"])))
            raise RuntimeError("project ASR provider execution failed") from None
        return {"status": state.status, "task_key": task_key, "created": True, "external_calls": calls,
                "project_private": True, "media_url_recorded": False, "storage_credentials_exposed": False}

    def _record_state(self, project_id: UUID, video_id: UUID, review_id: UUID, job_id: UUID,
                      state, currency: str) -> None:
        if state.status not in {"submitted", "running", "completed", "failed"}:
            raise ValueError("ASR provider returned invalid state")
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            if state.status == "completed" and state.evidence is not None:
                cost_id = _insert_cost(conn, project_id, video_id, job_id, state.cost, currency)
                conn.execute("update project_asr_execution_job set status='completed',provider_task_ref=%s,submission_count=1,task_cost_id=%s where id=%s",
                             (state.provider_task_ref, cost_id, job_id))
                evidence: TranscriptEvidence = state.evidence
                conn.execute("""insert into project_transcript(project_id,video_id,execution_job_id,media_review_id,
                              asr_provider,model_id,model_revision,engine_version,language,text_content,text_fingerprint,
                              segments,audio_duration_ms,task_cost_id)
                              values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (project_id, video_id, job_id, review_id, evidence.asr_provider, evidence.model_id,
                     evidence.model_revision, evidence.engine_version, evidence.language, evidence.text,
                     hashlib.sha256(evidence.text.encode()).hexdigest(), Jsonb([asdict(s) for s in evidence.segments]),
                     evidence.audio_duration_ms, cost_id))
            else:
                conn.execute("update project_asr_execution_job set status=%s,provider_task_ref=%s,submission_count=1,error_code=%s where id=%s",
                             (state.status, state.provider_task_ref, state.error_code, job_id))

    def _mark_failed(self, job_id: UUID) -> None:
        with psycopg.connect(self._dsn) as conn:
            conn.execute("""update project_asr_execution_job set status='failed',submission_count=1,
                         error_code='project_asr_provider_failed' where id=%s and status='submitting'""", (job_id,))

    def _assert_delivery_url(self, media_url: str) -> None:
        parsed = urlsplit(media_url)
        if (parsed.scheme != "https" or parsed.username is not None or parsed.password is not None
                or parsed.fragment or f"{parsed.scheme}://{parsed.netloc}" != self._delivery_origin):
            raise PermissionError("media URL does not match reviewed delivery origin")

    @staticmethod
    def _assert_dispatchable_video(conn, project_id: UUID, video_id: UUID) -> None:
        row = conn.execute("""select 1 from project_video_inclusion inclusion_row
                            join source_video video_row on video_row.id=inclusion_row.video_id
                            where inclusion_row.project_id=%s and inclusion_row.video_id=%s
                            and inclusion_row.status='accepted' and video_row.availability_status='available'""",
                           (project_id, video_id)).fetchone()
        if row is None:
            raise PermissionError("accepted available project video is required")

    def _asset(self, conn, video_id: UUID, asset_id: UUID) -> MediaAssetReference:
        row = conn.execute("""select id,video_id,kind,storage_location,bucket,object_key,content_sha256,size_bytes,
                           content_type,source_response_id,parent_asset_id from media_asset
                           where id=%s and video_id=%s and kind='audio' and content_type='audio/wav'""",
            (asset_id, video_id)).fetchone()
        if row is None:
            raise ValueError("project ASR requires normalized WAV asset belonging to video")
        return MediaAssetReference(**row)

    def _assert_member(self, conn, project_id: UUID, actor: str) -> None:
        row = conn.execute("""select 1 from research_project_member where project_id=%s and actor_id=%s
                           and effective_from <= now() and (effective_until is null or effective_until > now())""",
            (project_id, actor)).fetchone()
        if row is None:
            raise PermissionError("active project membership is required")

    def _assert_reviewer(self, conn, project_id: UUID, actor: str) -> None:
        if actor in _allowlist(self._variable(GLOBAL_REVIEWERS_PATH)):
            return
        row = conn.execute("""select 1 from research_project_member where project_id=%s and actor_id=%s
                           and role in ('owner','admin') and effective_from <= now()
                           and (effective_until is null or effective_until > now())""", (project_id, actor)).fetchone()
        if row is None:
            raise PermissionError("project owner/admin or global reviewer is required")

    @staticmethod
    def _actor() -> str:
        actor = os.environ.get("WM_END_USER_EMAIL", "").strip().lower()
        if not _ACTOR.fullmatch(actor):
            raise PermissionError("authenticated Windmill end-user identity is required")
        return actor


def asset_manifest(asset: MediaAssetReference, delivery_origin: str) -> str:
    """Hash every asset identity field that can affect ASR delivery, no secrets."""
    if asset.kind != "audio" or asset.content_type != "audio/wav" or not _SHA256.fullmatch(asset.content_sha256):
        raise ValueError("normalized WAV asset with SHA-256 is required")
    # Reuse L2's reviewed manifest contract, including the normalized actual
    # delivery origin.  This is intentionally not an ad-hoc asdict hash.
    return asset_fingerprint(asset, _origin(delivery_origin))


def _insert_cost(conn, project_id, video_id, job_id, cost: TaskCost | None, currency: str):
    value = cost or TaskCost(api_cost=None, asr_cost=None, llm_cost=Decimal("0"), currency=currency, basis="unknown")
    row = conn.execute("""insert into project_research_task_cost(project_id,video_id,task_key,task_type,task_version,status,
                        input_fingerprint,output_fingerprint,api_cost,asr_cost,llm_cost,cost_currency,cost_basis)
                        select project_id,video_id,task_key,'asr_transcription','project-asr-v1','completed',source_fingerprint,
                        source_fingerprint,%s,%s,%s,%s,%s from project_asr_execution_job where id=%s returning id""",
        (value.api_cost, value.asr_cost, value.llm_cost, value.currency, value.basis, job_id)).fetchone()
    return row["id"]


def _task_key(*parts: object) -> str:
    values = [str(part) for part in parts]
    return "project-asr-v1:" + hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()


def _ids(request: ProjectMediaReviewInput) -> tuple[UUID, UUID, UUID]:
    return _uuid(request.project_id, "project_id"), _uuid(request.video_id, "video_id"), _uuid(request.asset_id, "asset_id")


def _uuid(value: UUID | str, name: str) -> UUID:
    try: return UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc: raise ValueError(f"{name} must be a UUID") from exc


def _review_input(request: ProjectMediaReviewInput, fingerprint: str) -> None:
    if not isinstance(request.review_version, str) or not request.review_version.strip() or len(request.review_version) > 80:
        raise ValueError("review version is required")
    if not isinstance(request.review_statement, Mapping) or not request.review_statement:
        raise ValueError("human review statement is required")
    if not isinstance(fingerprint, str) or not _SHA256.fullmatch(fingerprint):
        raise ValueError("expected asset manifest fingerprint is required")


def _dispatch_input(request: ProjectASRDispatch) -> None:
    for name in ("provider", "model_id", "model_revision", "engine_version", "cost_currency"):
        value = getattr(request, name)
        if not isinstance(value, str) or not value.strip() or len(value) > 160:
            raise ValueError(f"{name} is required")
    if not isinstance(request.source_fingerprint, str) or not _SHA256.fullmatch(request.source_fingerprint):
        raise ValueError("source_fingerprint must be a SHA-256")


def _allowlist(value: str) -> frozenset[str]:
    try: raw = json.loads(value)
    except Exception: raise RuntimeError("global project ASR reviewer allowlist unavailable") from None
    if not isinstance(raw, list) or not raw:
        raise RuntimeError("global project ASR reviewer allowlist unavailable")
    emails = frozenset(item.strip().lower() for item in raw if isinstance(item, str) and _ACTOR.fullmatch(item.strip().lower()))
    if len(emails) != len(raw): raise RuntimeError("global project ASR reviewer allowlist invalid")
    return emails


def _windmill_variable(path: str) -> str:
    import wmill
    return wmill.get_variable(path)
