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
from douyin_research.providers.execution_contracts import ASR_ASYNC_CAPABILITY, validate_execution_contract
from douyin_research.providers.volcengine_asr import VOLCENGINE_ASR_PROVIDER


GLOBAL_REVIEWERS_PATH = "f/content_research/project_asr_global_reviewers"
IDENTITY_SOURCE = "windmill_end_user_email_allowlist_v1"
STANDING_REVIEW_VERSION_PREFIX = "standing-media-v1:"
STANDING_SCOPE = "accepted_available_public_video"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ACTOR = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+\Z")
_VIDEO_ID = re.compile(r"[0-9]+\Z")


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
    only for their own project. A listening preview is reviewer-only too:
    a private media URL must not be issued to every project reader.
    """

    def __init__(self, dsn: str, *, delivery_origin: str,
                 variable: Callable[[str], str] | None = None,
                 trusted_worker_actor: str | None = None,
                 trusted_storage_location: str | None = None,
                 trusted_bucket: str | None = None) -> None:
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("dsn is required")
        self._dsn = dsn
        self._delivery_origin = _origin(delivery_origin)
        self._variable = variable or _windmill_variable
        # A scheduled worker has no browser session. Its identity is fixed in
        # server configuration and must still be a global reviewer; no job or
        # Raw App argument can set it.
        if trusted_worker_actor is not None and not _ACTOR.fullmatch(trusted_worker_actor.strip().lower()):
            raise ValueError("trusted worker actor must be an email")
        self._trusted_worker_actor = trusted_worker_actor.strip().lower() if trusted_worker_actor else None
        self._trusted_storage_location = trusted_storage_location
        self._trusted_bucket = trusted_bucket

    def preview(self, request: ProjectMediaReviewInput) -> dict[str, object]:
        actor = self._human_actor()
        project_id, video_id, asset_id = _ids(request)
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            self._assert_reviewer(conn, project_id, actor)
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

    def list_assets(self, *, project_id: UUID | str, video_id: UUID | str,
                    review_version: str) -> dict[str, object]:
        """Reviewer-only inventory of normalized audio, without object keys."""
        actor = self._human_actor()
        project, video = _uuid(project_id, "project_id"), _uuid(video_id, "video_id")
        if not isinstance(review_version, str) or not review_version.strip() or len(review_version) > 80:
            raise ValueError("review version is required")
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            self._assert_reviewer(conn, project, actor)
            self._assert_dispatchable_video(conn, project, video)
            rows = conn.execute(
                """select id,video_id,kind,storage_location,bucket,object_key,
                          content_sha256,size_bytes,content_type,source_response_id,parent_asset_id
                   from media_asset where video_id=%s and kind='audio'
                     and content_type='audio/wav'
                   order by created_at desc,id desc limit 20""",
                (video,),
            ).fetchall()
            assets = []
            for row in rows:
                asset = MediaAssetReference(**row)
                manifest = asset_manifest(asset, self._delivery_origin)
                review = conn.execute(
                    """select review.id,review.status,review.asset_manifest_fingerprint,
                              review.authorization_kind,grant_row.status as grant_status
                       from project_asr_media_review review
                       left join project_asr_standing_grant grant_row on grant_row.id=review.standing_grant_id
                       where review.project_id=%s and review.asset_id=%s
                         and (review.review_version=%s or review.authorization_kind='standing_grant')
                       order by case when review.authorization_kind='standing_grant'
                                          and grant_row.status='active' and review.status='approved' then 0
                                     when review.review_version=%s and review.status='approved' then 1
                                     when review.status='approved' then 2 else 3 end,
                                review.created_at desc limit 1""",
                    (project, asset.id, review_version, review_version),
                ).fetchone()
                status = ("not_reviewed" if review is None else
                          "stale" if review["asset_manifest_fingerprint"] != manifest else
                          "grant_revoked" if review["authorization_kind"] == "standing_grant"
                              and review["grant_status"] != "active" else review["status"])
                assets.append({"asset_id": str(asset.id), "size_bytes": asset.size_bytes,
                               "content_sha256": asset.content_sha256,
                               "asset_manifest_fingerprint": manifest,
                               "review_id": str(review["id"]) if review else None,
                               "review_status": status, "delivery_origin": self._delivery_origin})
                if review is not None:
                    assets[-1]["authorization_kind"] = review["authorization_kind"]
        return {"project_id": str(project), "video_id": str(video),
                "review_version": review_version, "assets": assets,
                "external_calls": 0, "storage_credentials_exposed": False}

    def approve(self, request: ProjectMediaReviewInput, *, expected_manifest_fingerprint: str) -> dict[str, object]:
        actor = self._human_actor()
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

    def standing_grant(self, *, project_id: UUID | str) -> dict[str, object]:
        """Owner-visible project authorization, distinct from listening review."""
        actor, project = self._human_actor(), _uuid(project_id, "project_id")
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            self._assert_project_owner(conn, project, actor)
            row = conn.execute("""select id,status,grant_version,provider,source_scope,authorized_by,authorized_at,
                                 revoked_at from project_asr_standing_grant
                                 where project_id=%s and status='active' order by authorized_at desc limit 1""",
                               (project,)).fetchone()
        return {"project_id": str(project), "grant": _public_grant(row), "external_calls": 0}

    def authorize_standing(self, *, project_id: UUID | str) -> dict[str, object]:
        """Record the authenticated owner's standing ASR permission only."""
        actor, project = self._human_actor(), _uuid(project_id, "project_id")
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            self._assert_project_owner(conn, project, actor)
            active = conn.execute("""select 1 from research_project project_row
                                     join research_organization org_row on org_row.id=project_row.organization_id
                                     where project_row.id=%s and project_row.status='active'
                                       and org_row.status='active'""", (project,)).fetchone()
            if active is None:
                raise PermissionError("active project and organization required for standing ASR authorization")
            row = conn.execute("""insert into project_asr_standing_grant
                                  (project_id,provider,source_scope,authorized_by)
                                  values(%s,%s,%s,%s)
                                  on conflict (project_id) where status='active' do nothing
                                  returning id,status,grant_version,provider,source_scope,authorized_by,authorized_at,revoked_at""",
                               (project, VOLCENGINE_ASR_PROVIDER, STANDING_SCOPE, actor)).fetchone()
            if row is None:
                row = conn.execute("""select id,status,grant_version,provider,source_scope,authorized_by,authorized_at,revoked_at
                                      from project_asr_standing_grant where project_id=%s and status='active'""",
                                   (project,)).fetchone()
        return {"project_id": str(project), "grant": _public_grant(row), "external_calls": 0}

    def revoke_standing(self, *, project_id: UUID | str, grant_id: UUID | str) -> dict[str, object]:
        actor, project = self._human_actor(), _uuid(project_id, "project_id")
        grant = _uuid(grant_id, "grant_id")
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            self._assert_project_owner(conn, project, actor)
            row = conn.execute("""update project_asr_standing_grant set status='revoked',revoked_by=%s
                                  where id=%s and project_id=%s and status='active' returning id""",
                               (actor, grant, project)).fetchone()
            if row is None:
                raise ValueError("active project standing grant is required")
        return {"project_id": str(project), "grant_id": str(grant), "status": "revoked",
                "in_flight_tasks_may_still_be_billed": True, "external_calls": 0}

    def authorize_standing_asset(self, *, project_id: UUID | str, video_id: UUID | str,
                                 asset_id: UUID | str) -> dict[str, object]:
        """Worker binds one current WAV to an active grant; never asserts listening."""
        actor = self._execution_actor()
        project, video, asset_id = (_uuid(project_id, "project_id"), _uuid(video_id, "video_id"),
                                    _uuid(asset_id, "asset_id"))
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            self._assert_reviewer(conn, project, actor)
            self._assert_dispatchable_video(conn, project, video)
            source = conn.execute("""select platform,platform_video_id,source_url
                                    from source_video where id=%s""", (video,)).fetchone()
            if source is None or not _public_douyin_video(source):
                raise PermissionError("standing ASR requires a verified public Douyin video URL")
            grant = conn.execute("""select id from project_asr_standing_grant
                                 where project_id=%s and provider=%s and source_scope=%s
                                   and status='active' for update""",
                                 (project, VOLCENGINE_ASR_PROVIDER, STANDING_SCOPE)).fetchone()
            if grant is None:
                raise PermissionError("active project standing ASR grant is required")
            standing_version = STANDING_REVIEW_VERSION_PREFIX + str(grant["id"])
            asset = self._asset(conn, video, asset_id)
            self._assert_standing_asset_provenance(conn, video, asset)
            if conn.execute("""select 1 from project_asr_media_review
                               where project_id=%s and asset_id=%s and authorization_kind='listened'
                                 and status='approved' limit 1""",
                            (project, asset_id)).fetchone():
                return {"status": "already_listened_approved", "external_calls": 0}
            if conn.execute("""select 1 from project_asr_execution_job
                               where project_id=%s and video_id=%s and media_fingerprint=%s limit 1""",
                            (project, video, asset.content_sha256)).fetchone():
                return {"status": "already_attempted", "external_calls": 0}
            manifest = asset_manifest(asset, self._delivery_origin)
            row = conn.execute("""insert into project_asr_media_review(
                                   project_id,video_id,asset_id,review_version,media_fingerprint,
                                   asset_manifest_fingerprint,delivery_origin,identity_source,
                                   review_statement,authorization_kind,standing_grant_id)
                                 values(%s,%s,%s,%s,%s,%s,%s,%s,%s,'standing_grant',%s)
                                 on conflict(project_id,asset_id,review_version) do nothing returning id""",
                               (project, video, asset_id, standing_version, asset.content_sha256,
                                manifest, self._delivery_origin, IDENTITY_SOURCE,
                                Jsonb({"listening_status": "not_listened", "consent_basis": "project_standing_grant"}),
                                grant["id"])).fetchone()
            if row is None:
                row = conn.execute("""select id,status,standing_grant_id,asset_manifest_fingerprint
                                      from project_asr_media_review where project_id=%s and asset_id=%s
                                        and review_version=%s for update""",
                                   (project, asset_id, standing_version)).fetchone()
                if (row is None or row["status"] != "approved" or row["standing_grant_id"] != grant["id"]
                        or row["asset_manifest_fingerprint"] != manifest):
                    raise PermissionError("existing standing asset authorization is unavailable or stale")
            else:
                conn.execute("""update project_asr_media_review set status='approved'
                                where id=%s and status='draft'""", (row["id"],))
        return {"status": "approved", "review_id": str(row["id"]), "grant_id": str(grant["id"]),
                "listening_status": "not_listened", "external_calls": 0}

    def revoke(self, *, project_id: UUID | str, media_review_id: UUID | str) -> dict[str, object]:
        actor = self._human_actor()
        project_id, review_id = _uuid(project_id, "project_id"), _uuid(media_review_id, "media_review_id")
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            self._assert_reviewer(conn, project_id, actor)
            review_scope = conn.execute(
                "select video_id from project_asr_media_review where id=%s and project_id=%s",
                (review_id, project_id),
            ).fetchone()
            if review_scope is None:
                raise ValueError("project media review is unavailable or already revoked")
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
        actor = self._execution_actor()
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
            self._assert_standing_grant(conn, review, project_id, request.provider)
            if review["authorization_kind"] == "standing_grant":
                source = conn.execute("""select platform,platform_video_id,source_url from source_video
                                        where id=%s""", (video_id,)).fetchone()
                if source is None or not _public_douyin_video(source):
                    raise PermissionError("standing ASR requires a verified public Douyin video URL")
            asset = self._asset(conn, video_id, review["asset_id"])
            if review["authorization_kind"] == "standing_grant":
                self._assert_standing_asset_provenance(conn, video_id, asset)
            manifest = asset_manifest(asset, self._delivery_origin)
            if review["asset_manifest_fingerprint"] != manifest or review["media_fingerprint"] != asset.content_sha256:
                raise PermissionError("approved project media review is stale")
            if request.source_fingerprint != asset.content_sha256:
                raise PermissionError("ASR source fingerprint must match reviewed project asset")
            task_key = _task_key(project_id, video_id, review_id, review["asset_id"], review["review_version"],
                                 asset.content_sha256, manifest, request.provider, request.source_fingerprint,
                                 request.model_id, request.model_revision, request.engine_version)
            existing = conn.execute("select status from project_asr_execution_job where task_key=%s", (task_key,)).fetchone()
            if existing is not None:
                return {"status": "reconciliation_required" if existing["status"] == "submitting" else existing["status"], "task_key": task_key, "created": False,
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
        submit_started = False
        try:
            # Keep the final approval lock through the one submit HTTP call.
            # A concurrent revoke therefore either wins before this recheck
            # (and no request is made), or waits until this exact submit ends.
            with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
                self._assert_current_review(conn, project_id, video_id, review_id, request, manifest,
                                            expected_review_version=review["review_version"])
                media_url = media_url_factory(asset)
                self._assert_delivery_url(media_url)
                provider = provider_factory()
                self._assert_provider(provider, request)
                submit_started = True
                state = provider.submit(ASRProviderRequest(task_key=task_key, media_ref=media_url,
                    source_fingerprint=request.source_fingerprint, model_id=request.model_id,
                    model_revision=request.model_revision, engine_version=request.engine_version))
                self._record_state_in_connection(conn, project_id, video_id, review_id, UUID(str(job["id"])), state,
                                                 request.cost_currency)
            calls = 1
        except Exception:
            if not submit_started:
                self._mark_pre_submit_cancelled(UUID(str(job["id"])))
                return {"status": "cancelled", "error_code": "project_asr_pre_submit_rejected",
                        "task_key": task_key, "created": True, "external_calls": 0,
                        "project_private": True, "media_url_recorded": False}
            # A transport exception after submit starts is ambiguous. Keep
            # the task for reconciliation; never create a second paid task.
            self._mark_reconciliation_required(UUID(str(job["id"])))
            return {"status": "reconciliation_required", "task_key": task_key, "created": True,
                    "external_calls": 1, "project_private": True, "media_url_recorded": False}
        return {"status": "failed" if _no_speech(state) else state.status,
                "error_code": "project_asr_no_speech" if _no_speech(state) else state.error_code,
                "task_key": task_key, "created": True, "external_calls": calls,
                "project_private": True, "media_url_recorded": False, "storage_credentials_exposed": False}

    def resume_poll(self, *, project_id: UUID | str, task_key: str,
                    provider_factory: Callable[[], ProjectASRProvider]) -> dict[str, object]:
        """Bounded scheduler resume: poll an existing task only, never submit."""
        actor, project_id = self._execution_actor(), _uuid(project_id, "project_id")
        calls = 0
        try:
            # The review and job lock stay held through the one poll HTTP call,
            # matching submit's revocation-before-HTTP boundary.
            with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
                self._assert_reviewer(conn, project_id, actor)
                job = conn.execute("""select * from project_asr_execution_job where project_id=%s and task_key=%s
                                  and status in ('submitted','running') and provider_task_ref is not null for update""",
                                   (project_id, task_key)).fetchone()
                if job is None:
                    return {"status": "reconciliation_required", "external_calls": 0, "created": False}
                self._assert_dispatchable_video(conn, project_id, job["video_id"])
                review = conn.execute("""select status,asset_manifest_fingerprint from project_asr_media_review
                                       where id=%s for update""", (job["media_review_id"],)).fetchone()
                if review is None or review["status"] != "approved" or review["asset_manifest_fingerprint"] != job["asset_manifest_fingerprint"]:
                    return {"status": "reconciliation_required", "external_calls": 0, "created": False}
                request = ProjectASRDispatch(project_id, job["video_id"], job["media_review_id"], job["provider"],
                                             job["model_id"], job["model_revision"], job["engine_version"], job["source_fingerprint"], job["cost_currency"])
                provider = provider_factory()
                self._assert_provider(provider, request)
                state = provider.poll(job["provider_task_ref"]); calls = 1
                if state.provider_task_ref != job["provider_task_ref"]:
                    raise ValueError("ASR provider task reference changed during polling")
                self._record_state_in_connection(conn, project_id, job["video_id"], job["media_review_id"], job["id"],
                                                 state, job["cost_currency"], counted_poll=True)
        except Exception:
            return {"status": "reconciliation_required", "external_calls": calls, "created": False}
        return {"status": "failed" if _no_speech(state) else state.status,
                "error_code": "project_asr_no_speech" if _no_speech(state) else state.error_code,
                "external_calls": calls, "created": False, "project_private": True}

    def _record_state(self, project_id: UUID, video_id: UUID, review_id: UUID, job_id: UUID,
                      state, currency: str, *, counted_poll: bool = False) -> None:
        if state.status not in {"submitted", "running", "completed", "failed"}:
            raise ValueError("ASR provider returned invalid state")
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            self._record_state_in_connection(conn, project_id, video_id, review_id, job_id, state, currency,
                                             counted_poll=counted_poll)

    @staticmethod
    def _record_state_in_connection(conn, project_id: UUID, video_id: UUID, review_id: UUID, job_id: UUID,
                                    state, currency: str, *, counted_poll: bool = False) -> None:
        if state.status not in {"submitted", "running", "completed", "failed"}:
            raise ValueError("ASR provider returned invalid state")
        if state.status == "completed" and state.evidence is None:
            raise ValueError("completed ASR provider state requires evidence")
        if _no_speech(state):
            cost_id = _insert_cost(conn, project_id, video_id, job_id, state.cost, currency, status="failed")
            conn.execute("""update project_asr_execution_job
                         set status='failed', provider_task_ref=%s, submission_count=1,
                             task_cost_id=%s, error_code='project_asr_no_speech',
                             poll_count=poll_count+%s where id=%s""",
                         (state.provider_task_ref, cost_id, int(counted_poll), job_id))
            return
        if state.status == "completed" and state.evidence is not None:
            cost_id = _insert_cost(conn, project_id, video_id, job_id, state.cost, currency)
            conn.execute("""update project_asr_execution_job
                             set status='completed', provider_task_ref=%s, submission_count=1, task_cost_id=%s,
                                 poll_count=poll_count+%s where id=%s""",
                             (state.provider_task_ref, cost_id, int(counted_poll), job_id))
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
            cost_id = (_insert_cost(conn, project_id, video_id, job_id, state.cost, currency, status="failed")
                       if state.status == "failed" else None)
            conn.execute("""update project_asr_execution_job
                             set status=%s, provider_task_ref=%s, submission_count=1, error_code=%s,
                                 task_cost_id=coalesce(task_cost_id,%s), poll_count=poll_count+%s where id=%s""",
                             (state.status, state.provider_task_ref, state.error_code, cost_id,
                              int(counted_poll), job_id))

    def _mark_reconciliation_required(self, job_id: UUID) -> None:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            row = conn.execute("""select project_id,video_id,cost_currency from project_asr_execution_job
                                 where id=%s and status='submitting' for update""", (job_id,)).fetchone()
            if row is None:
                return
            cost_id = _insert_cost(conn, row["project_id"], row["video_id"], job_id, None,
                                   row["cost_currency"], status="failed")
            conn.execute("""update project_asr_execution_job set submission_count=1,
                         task_cost_id=%s,error_code='project_asr_reconciliation_required'
                         where id=%s and status='submitting'""", (cost_id, job_id))

    def _mark_pre_submit_cancelled(self, job_id: UUID) -> None:
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            conn.execute("""update project_asr_execution_job
                            set status='cancelled',error_code='project_asr_pre_submit_rejected'
                            where id=%s and status='submitting' and submission_count=0""", (job_id,))

    def _assert_current_review(self, conn, project_id: UUID, video_id: UUID, review_id: UUID,
                               request: ProjectASRDispatch, manifest: str, *,
                               expected_review_version: str) -> None:
        review = conn.execute("""select status,review_version,asset_id,media_fingerprint,asset_manifest_fingerprint,
                                      authorization_kind,standing_grant_id
                               from project_asr_media_review where id=%s and project_id=%s and video_id=%s for update""",
                              (review_id, project_id, video_id)).fetchone()
        if (review is None or review["status"] != "approved"
                or review["review_version"] != expected_review_version):
            raise PermissionError("approved project media review was revoked before ASR HTTP")
        if (review["media_fingerprint"] != request.source_fingerprint
                or review["asset_manifest_fingerprint"] != manifest):
            raise PermissionError("approved project media review changed before ASR HTTP")
        asset = self._asset(conn, video_id, review["asset_id"], lock=True)
        if asset.content_sha256 != request.source_fingerprint or asset_manifest(asset, self._delivery_origin) != manifest:
            raise PermissionError("project ASR asset changed before provider HTTP")
        self._assert_standing_grant(conn, review, project_id, request.provider)
        self._lock_dispatchable_video(conn, project_id, video_id)
        if review["authorization_kind"] == "standing_grant":
            source = conn.execute("""select platform,platform_video_id,source_url from source_video
                                    where id=%s""", (video_id,)).fetchone()
            if source is None or not _public_douyin_video(source):
                raise PermissionError("public Douyin source changed before ASR HTTP")
            self._assert_standing_asset_provenance(conn, video_id, asset)

    @staticmethod
    def _assert_standing_grant(conn, review, project_id: UUID, provider: str) -> None:
        if review["authorization_kind"] == "listened":
            return
        if review["authorization_kind"] != "standing_grant" or review["standing_grant_id"] is None:
            raise PermissionError("ASR authorization provenance is invalid")
        grant = conn.execute("""select id from project_asr_standing_grant
                                where id=%s and project_id=%s and provider=%s
                                  and source_scope=%s and status='active' for update""",
                             (review["standing_grant_id"], project_id, provider, STANDING_SCOPE)).fetchone()
        if grant is None:
            raise PermissionError("standing ASR grant was revoked before provider HTTP")

    @staticmethod
    def _lock_dispatchable_video(conn, project_id: UUID, video_id: UUID) -> None:
        # Rows are locked through the submit HTTP call.  A concurrent removal,
        # takedown, or project suspension must finish before or after submit.
        rows = (
            ("select status from project_video_inclusion where project_id=%s and video_id=%s for update",
             (project_id, video_id), "accepted"),
            ("select availability_status as status from source_video where id=%s for update",
             (video_id,), "available"),
            ("select status from research_project where id=%s for update", (project_id,), "active"),
        )
        for statement, params, expected in rows:
            row = conn.execute(statement, params).fetchone()
            if row is None or row["status"] != expected:
                raise PermissionError("project video is no longer dispatchable before ASR HTTP")
        org = conn.execute("""select org_row.status from research_organization org_row
                              join research_project project_row on project_row.organization_id=org_row.id
                              where project_row.id=%s for update of org_row""", (project_id,)).fetchone()
        if org is None or org["status"] != "active":
            raise PermissionError("project organization is no longer active before ASR HTTP")

    def _assert_delivery_url(self, media_url: str) -> None:
        parsed = urlsplit(media_url)
        if (parsed.scheme != "https" or parsed.username is not None or parsed.password is not None
                or parsed.fragment or f"{parsed.scheme}://{parsed.netloc}" != self._delivery_origin):
            raise PermissionError("media URL does not match reviewed delivery origin")

    @staticmethod
    def _assert_dispatchable_video(conn, project_id: UUID, video_id: UUID) -> None:
        row = conn.execute("""select 1 from project_video_inclusion inclusion_row
                            join source_video video_row on video_row.id=inclusion_row.video_id
                            join research_project project_row on project_row.id=inclusion_row.project_id
                            join research_organization org_row on org_row.id=project_row.organization_id
                            where inclusion_row.project_id=%s and inclusion_row.video_id=%s
                            and inclusion_row.status='accepted' and video_row.availability_status='available'
                            and project_row.status='active' and org_row.status='active'""",
                           (project_id, video_id)).fetchone()
        if row is None:
            raise PermissionError("accepted available project video is required")

    def _asset(self, conn, video_id: UUID, asset_id: UUID, *, lock: bool = False) -> MediaAssetReference:
        statement = """select id,video_id,kind,storage_location,bucket,object_key,content_sha256,size_bytes,
                       content_type,source_response_id,parent_asset_id from media_asset
                       where id=%s and video_id=%s and kind='audio' and content_type='audio/wav'"""
        row = conn.execute(statement + (" for update" if lock else ""),
            (asset_id, video_id)).fetchone()
        if row is None:
            raise ValueError("project ASR requires normalized WAV asset belonging to video")
        return MediaAssetReference(**row)

    def _assert_standing_asset_provenance(self, conn, video_id: UUID,
                                          asset: MediaAssetReference) -> None:
        if (not self._trusted_storage_location or not self._trusted_bucket
                or asset.storage_location != self._trusted_storage_location
                or asset.bucket != self._trusted_bucket or asset.parent_asset_id is None):
            raise PermissionError("standing ASR requires trusted, parent-linked media storage")
        row = conn.execute("""select 1 from media_asset parent
                              join media_asset asset_source on asset_source.id=%s
                                and asset_source.video_id=parent.video_id
                                and asset_source.parent_asset_id=parent.id
                              join pipeline_run_item item on item.entity_type='video' and item.entity_id=%s
                              join pipeline_run run on run.id=item.run_id
                              where parent.id=%s and parent.video_id=%s and parent.kind='video'
                                and parent.content_type='video/mp4'
                                and parent.storage_location=%s and parent.bucket=%s
                                and (parent.source_response_id is null or asset_source.source_response_id is null
                                     or parent.source_response_id=asset_source.source_response_id)
                                and run.run_type='media_ingestion' and run.status='success'
                                and run.platform='douyin' and item.stage='media'
                                and item.outcome in ('success','reused')
                                and item.metadata->'asset_ids' @> jsonb_build_array(parent.id::text,%s::text)
                              limit 1 for update of parent,asset_source,item,run""",
                           (asset.id, video_id, asset.parent_asset_id, video_id,
                            self._trusted_storage_location, self._trusted_bucket, asset.id)).fetchone()
        if row is None:
            raise PermissionError("standing ASR audio lacks successful public-video ingestion provenance")

    def _assert_member(self, conn, project_id: UUID, actor: str) -> None:
        row = conn.execute("""select 1 from (select distinct on (actor_id) * from research_project_member
                           where project_id=%s and actor_id=%s and effective_from <= now()
                           order by actor_id,effective_from desc) latest where status='active'
                           and (effective_until is null or effective_until > now())""",
            (project_id, actor)).fetchone()
        if row is None:
            raise PermissionError("active project membership is required")

    def _assert_reviewer(self, conn, project_id: UUID, actor: str) -> None:
        row = conn.execute("""select 1 from (select distinct on (actor_id) * from research_project_member
                           where project_id=%s and actor_id=%s and effective_from <= now()
                           order by actor_id,effective_from desc) latest where status='active'
                           and role in ('owner','admin') and (effective_until is null or effective_until > now())""", (project_id, actor)).fetchone()
        # Project ownership is sufficient on its own.  Do this database-only
        # check before reading the optional cross-project reviewer variable:
        # an absent Windmill variable must not prevent an owner/admin from
        # reviewing their own private media.
        if row is not None:
            return
        # Do not treat a missing, malformed, or unreachable variable as an
        # empty allowlist or as authorization.  _allowlist deliberately raises
        # in those cases, so non-members remain fail-closed.
        if actor in _allowlist(self._variable(GLOBAL_REVIEWERS_PATH)):
            return
        raise PermissionError("project owner/admin or global reviewer is required")

    @staticmethod
    def _assert_project_owner(conn, project_id: UUID, actor: str) -> None:
        row = conn.execute("""select 1 from (select distinct on (actor_id) * from research_project_member
                              where project_id=%s and actor_id=%s and effective_from <= now()
                              order by actor_id,effective_from desc) latest
                              where status='active' and role='owner'
                                and (effective_until is null or effective_until > now())""",
                           (project_id, actor)).fetchone()
        if row is None:
            raise PermissionError("project owner is required for standing ASR authorization")

    @staticmethod
    def _human_actor() -> str:
        actor = os.environ.get("WM_END_USER_EMAIL", "").strip().lower()
        if _ACTOR.fullmatch(actor):
            return actor
        raise PermissionError("authenticated Windmill end-user identity is required")

    def _execution_actor(self) -> str:
        # A worker constructed with the server-configured identity must keep
        # that identity even if Windmill injects a valid publisher/end-user
        # email into the scheduled job environment. Human review entrypoints
        # construct the service without trusted_worker_actor.
        if self._trusted_worker_actor is not None:
            return self._trusted_worker_actor
        return self._human_actor()

    @staticmethod
    def _assert_provider(provider: ProjectASRProvider, request: ProjectASRDispatch) -> None:
        if getattr(provider, "provider_name", None) != request.provider or getattr(provider, "max_retries", None) != 0:
            raise ValueError("project ASR provider identity or retries do not match dispatch")
        validate_execution_contract(provider.contract, expected_provider=request.provider,
            expected_capability=ASR_ASYNC_CAPABILITY, expected_model_id=request.model_id,
            expected_model_revision=request.model_revision, expected_currency=request.cost_currency)


def asset_manifest(asset: MediaAssetReference, delivery_origin: str) -> str:
    """Hash every asset identity field that can affect ASR delivery, no secrets."""
    if asset.kind != "audio" or asset.content_type != "audio/wav" or not _SHA256.fullmatch(asset.content_sha256):
        raise ValueError("normalized WAV asset with SHA-256 is required")
    # Reuse L2's reviewed manifest contract, including the normalized actual
    # delivery origin.  This is intentionally not an ad-hoc asdict hash.
    return asset_fingerprint(asset, _origin(delivery_origin))


def _public_grant(row) -> dict[str, object] | None:
    if row is None:
        return None
    return {"id": str(row["id"]), "status": row["status"], "grant_version": row["grant_version"],
            "provider": row["provider"],
            "source_scope": row["source_scope"], "authorized_by": row["authorized_by"],
            "authorized_at": row["authorized_at"].isoformat(),
            "revoked_at": row["revoked_at"].isoformat() if row["revoked_at"] else None}


def _public_douyin_video(row: Mapping[str, object]) -> bool:
    """Require an exact public-post URL, not a private or arbitrary media URL."""
    if row["platform"] != "douyin":
        return False
    video_id, source_url = row["platform_video_id"], row["source_url"]
    if not isinstance(video_id, str) or not _VIDEO_ID.fullmatch(video_id) or not isinstance(source_url, str):
        return False
    try:
        parsed = urlsplit(source_url)
        path_by_host = {
            "douyin.com": f"/video/{video_id}",
            "www.douyin.com": f"/video/{video_id}",
            "iesdouyin.com": f"/share/video/{video_id}/",
            "www.iesdouyin.com": f"/share/video/{video_id}/",
        }
        return (parsed.scheme == "https" and parsed.path == path_by_host.get(parsed.hostname)
                and parsed.username is None and parsed.password is None and parsed.port is None
                and not parsed.fragment)
    except ValueError:
        return False


def _insert_cost(conn, project_id, video_id, job_id, cost: TaskCost | None, currency: str, *, status: str = "completed"):
    value = cost or TaskCost(api_cost=None, asr_cost=None, llm_cost=Decimal("0"), currency=currency, basis="unknown")
    if status not in {"completed", "failed"}:
        raise ValueError("project ASR cost status is invalid")
    row = conn.execute("""insert into project_research_task_cost(project_id,video_id,task_key,task_type,task_version,status,
                        input_fingerprint,output_fingerprint,api_cost,asr_cost,llm_cost,cost_currency,cost_basis)
                        select project_id,video_id,task_key,'asr_transcription','project-asr-v1',%s,source_fingerprint,
                        case when %s='completed' then source_fingerprint else null end,
                        %s,%s,%s,%s,%s from project_asr_execution_job where id=%s returning id""",
        (status, status, value.api_cost, value.asr_cost, value.llm_cost, value.currency, value.basis, job_id)).fetchone()
    return row["id"]


def _no_speech(state) -> bool:
    return (state.status == "completed" and state.evidence is not None
            and (state.evidence.quality_status == "no_speech" or not state.evidence.text.strip()))


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
