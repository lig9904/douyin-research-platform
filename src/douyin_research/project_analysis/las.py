"""Project-private LAS permission and exactly-once asynchronous execution.

No method accepts an API key, delivery URL, actor email or cost as a browser
argument. The trusted worker supplies its configured storage and provider;
only an authenticated project owner/admin may grant or revoke video permission.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Protocol
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from douyin_research.media_assets import MediaAssetReference
from douyin_research.media_storage import PrivateS3MediaStorage, StoredMediaObject
from douyin_research.providers.volcengine_las import (
    LAS_MODEL_ID, LAS_OPERATOR_ID, LAS_OPERATOR_VERSION, LAS_TEMPLATE,
    LASPoll, LASSubmission, ReviewedLASVideoDelivery,
)


_ACTOR = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+\Z")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,79}\Z")
_SCOPE = re.compile(r"[A-Za-z0-9._:-]{1,160}\Z")
_PROVIDER = "volcengine_las"
_VERSIONED_REVIEW = "las-video-v2"
_MACHINE_AUTHORIZATION = "las-machine-v1"


def _is_versioned_review(value: str) -> bool:
    return value == _VERSIONED_REVIEW or value.startswith(_VERSIONED_REVIEW + "-")


class LASClient(Protocol):
    max_retries: int

    def submit(self, delivery: ReviewedLASVideoDelivery) -> LASSubmission: ...

    def poll(self, task_id: str) -> LASPoll: ...


def _uuid(value: UUID | str, name: str) -> UUID:
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"{name} must be a UUID") from exc


def _origin(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or
            parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise ValueError("LAS delivery origin must be an HTTPS origin")
    return f"{parsed.scheme}://{parsed.netloc}"


def video_manifest(asset: MediaAssetReference, delivery_origin: str,
                   object_version_id: str | None = None) -> str:
    """Bind all fields that determine which private video is delivered."""
    if asset.kind != "video" or not asset.content_type.startswith("video/"):
        raise ValueError("stored video asset required")
    fields = {**asdict(asset), "id": str(asset.id), "video_id": str(asset.video_id),
              "parent_asset_id": str(asset.parent_asset_id) if asset.parent_asset_id else None,
              "delivery_origin": _origin(delivery_origin), "contract": "project-las-video-v1"}
    if object_version_id is not None:
        _checked_version_id(object_version_id)
        fields.update(contract="project-las-video-v2", object_version_id=object_version_id,
                      purpose="volcengine_las_whole_video_understanding")
    return hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _checked_version_id(value: str) -> str:
    if (not isinstance(value, str) or not 1 <= len(value) <= 512 or
            value != value.strip() or value.lower() == "null"):
        raise ValueError("concrete S3 object VersionId required")
    return value


def _stored(asset: MediaAssetReference) -> StoredMediaObject:
    return StoredMediaObject(key=asset.object_key, sha256=asset.content_sha256,
                             size=asset.size_bytes, content_type=asset.content_type)


class ProjectLASService:
    """Human approval and trusted-worker dispatch, with no automatic Submit retry."""

    def __init__(self, dsn: str, *, delivery_origin: str,
                 trusted_worker_actor: str | None = None,
                 trusted_storage_location: str | None = None,
                 trusted_bucket: str | None = None,
                 account_scope: str | None = None) -> None:
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("database DSN required")
        self._dsn = dsn
        self._delivery_origin = _origin(delivery_origin)
        if trusted_worker_actor is not None and not _ACTOR.fullmatch(trusted_worker_actor):
            raise ValueError("trusted worker actor must be an email")
        if account_scope is not None and (not isinstance(account_scope, str) or
                                          not _SCOPE.fullmatch(account_scope)):
            raise ValueError("LAS account scope invalid")
        self._worker_actor = trusted_worker_actor
        self._storage_location = trusted_storage_location
        self._bucket = trusted_bucket
        self._account_scope = account_scope

    @staticmethod
    def _human_actor() -> str:
        actor = os.environ.get("WM_END_USER_EMAIL", "").strip().lower()
        if not _ACTOR.fullmatch(actor):
            raise PermissionError("authenticated Windmill end-user identity required")
        return actor

    @staticmethod
    def _assert_manager(conn: psycopg.Connection, project: UUID, actor: str) -> None:
        row = conn.execute(
            """select 1 from (select distinct on (actor_id) * from research_project_member
                 where project_id=%s and actor_id=%s and effective_from<=now()
                 order by actor_id,effective_from desc) latest
               where status='active' and role in ('owner','admin')
                 and (effective_until is null or effective_until>now())""",
            (project, actor),
        ).fetchone()
        if row is None:
            raise PermissionError("project owner/admin required for LAS review")

    @staticmethod
    def _lock_project(conn: psycopg.Connection, project: UUID) -> None:
        if conn.execute("select 1 from research_project where id=%s for update",
                        (project,)).fetchone() is None:
            raise PermissionError("project unavailable")

    @staticmethod
    def _lock_video(conn: psycopg.Connection, project: UUID, video: UUID) -> None:
        row = conn.execute(
            """select 1 from project_video_inclusion inclusion_row
               join source_video source_row on source_row.id=inclusion_row.video_id
               join research_project project_row on project_row.id=inclusion_row.project_id
               join research_organization org_row on org_row.id=project_row.organization_id
               where inclusion_row.project_id=%s and inclusion_row.video_id=%s
                 and inclusion_row.status='accepted' and source_row.availability_status='available'
                 and project_row.status='active' and org_row.status='active'
               for update of inclusion_row,source_row,project_row,org_row""",
            (project, video),
        ).fetchone()
        if row is None:
            raise PermissionError("active project and accepted available video required")

    @staticmethod
    def _asset(conn: psycopg.Connection, video: UUID, asset_id: UUID) -> MediaAssetReference:
        row = conn.execute(
            """select id,video_id,kind,storage_location,bucket,object_key,content_sha256,
                      size_bytes,content_type,source_response_id,parent_asset_id
               from media_asset where id=%s and video_id=%s and kind='video' for update""",
            (asset_id, video),
        ).fetchone()
        if row is None:
            raise ValueError("stored video asset unavailable")
        return MediaAssetReference(**row)

    def preview(self, *, project_id: UUID | str, video_id: UUID | str,
                asset_id: UUID | str, review_version: str,
                storage: PrivateS3MediaStorage | None = None) -> dict[str, object]:
        actor = self._human_actor()
        project, video, asset_key = (_uuid(project_id, "project_id"), _uuid(video_id, "video_id"),
                                     _uuid(asset_id, "asset_id"))
        if not isinstance(review_version, str) or not _VERSION.fullmatch(review_version):
            raise ValueError("LAS review version invalid")
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            self._assert_manager(conn, project, actor)
            self._lock_video(conn, project, video)
            asset = self._asset(conn, video, asset_key)
            current = conn.execute(
                """select id,status,asset_manifest_fingerprint,object_version_id
                   from project_las_video_review
                   where project_id=%s and asset_id=%s and review_version=%s""",
                (project, asset_key, review_version),
            ).fetchone()
        object_version_id = None
        if storage is not None:
            if storage.bucket != asset.bucket:
                raise PermissionError("LAS storage bucket mismatch")
            object_version_id = storage.verified_version(_stored(asset))
        elif _is_versioned_review(review_version):
            raise ValueError("trusted storage required for LAS byte preview")
        manifest = video_manifest(asset, self._delivery_origin,
                                  object_version_id if _is_versioned_review(review_version) else None)
        # Streaming the video occurs outside the database transaction. Recheck
        # the project, asset and review after the read so its URL/fingerprint
        # cannot be issued for an asset that changed during verification.
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            self._assert_manager(conn, project, actor)
            self._lock_video(conn, project, video)
            if self._asset(conn, video, asset_key) != asset:
                raise ValueError("LAS video asset changed during byte preview")
        return {"project_id": str(project), "video_id": str(video), "asset_id": str(asset_key),
                "asset_sha256": asset.content_sha256, "size_bytes": asset.size_bytes,
                "delivery_origin": self._delivery_origin, "review_version": review_version,
                "asset_manifest_fingerprint": manifest, "manifest_fingerprint": manifest,
                "object_version_id": object_version_id, "operator_id": LAS_OPERATOR_ID,
                "template_id": LAS_TEMPLATE, "model_id": LAS_MODEL_ID,
                "review_id": str(current["id"]) if current else None,
                "review_status": ("stale" if current and
                                  (current["asset_manifest_fingerprint"] != manifest or
                                   current["object_version_id"] != object_version_id)
                                  else current["status"] if current else "not_reviewed"),
                "external_calls": 0}

    def list_assets(self, *, project_id: UUID | str, video_id: UUID | str,
                    review_version: str) -> dict[str, object]:
        actor = self._human_actor()
        project, video = _uuid(project_id, "project_id"), _uuid(video_id, "video_id")
        if not isinstance(review_version, str) or not _VERSION.fullmatch(review_version):
            raise ValueError("LAS review version invalid")
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            self._assert_manager(conn, project, actor)
            self._lock_video(conn, project, video)
            assets = conn.execute(
                """select id,video_id,kind,storage_location,bucket,object_key,
                          content_sha256,size_bytes,content_type,source_response_id,parent_asset_id
                   from media_asset where video_id=%s and kind='video'
                   order by created_at desc,id desc limit 20""", (video,),
            ).fetchall()
            rows = []
            for asset_row in assets:
                asset = MediaAssetReference(**asset_row)
                review = conn.execute(
                    """select id,status,asset_manifest_fingerprint,object_version_id,binding_scheme
                       from project_las_video_review
                       where project_id=%s and asset_id=%s and review_version=%s""",
                    (project, asset.id, review_version),
                ).fetchone()
                authorization = conn.execute(
                    """select id,status,asset_manifest_fingerprint,object_version_id
                       from project_las_video_authorization
                       where project_id=%s and asset_id=%s
                         and authorization_version=%s
                       order by created_at desc,id desc limit 1""",
                    (project, asset.id, _MACHINE_AUTHORIZATION),
                ).fetchone()
                manifest = (video_manifest(asset, self._delivery_origin,
                                           review["object_version_id"])
                            if review or not _is_versioned_review(review_version) else None)
                rows.append({"asset_id": str(asset.id), "asset_sha256": asset.content_sha256,
                             "size_bytes": asset.size_bytes, "asset_manifest_fingerprint": manifest,
                             "object_version_id": review["object_version_id"] if review else None,
                             "review_id": str(review["id"]) if review else None,
                             "machine_authorization_id": (str(authorization["id"])
                                                          if authorization else None),
                             "machine_authorization_status": (
                                 "stale" if authorization and authorization["status"] == "authorized" and
                                 authorization["asset_manifest_fingerprint"] != video_manifest(
                                     asset, self._delivery_origin,
                                     authorization["object_version_id"])
                                 else authorization["status"] if authorization else "not_authorized"),
                             "review_status": ("legacy_unbound" if review and
                                               _is_versioned_review(review_version) and
                                               review["binding_scheme"] != "versioned_bytes_v2"
                                               else "stale" if review and
                                               review["asset_manifest_fingerprint"] != manifest
                                               else review["status"] if review else "not_reviewed")})
        return {"project_id": str(project), "video_id": str(video),
                "review_version": review_version, "assets": rows, "external_calls": 0}

    def status(self, *, project_id: UUID | str, video_id: UUID | str) -> dict[str, object]:
        """Project-member readback; machine summary never becomes human Case."""
        actor = self._human_actor()
        project, video = _uuid(project_id, "project_id"), _uuid(video_id, "video_id")
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            if not conn.execute(
                "select project_video_can_read(%s,%s,%s) as allowed",
                (project, actor, video),
            ).fetchone()["allowed"]:
                raise PermissionError("project LAS result access denied")
            rows = conn.execute(
                """select attempt.id,attempt.status,attempt.error_code,attempt.created_at,
                          attempt.provider_task_ref,attempt.asset_sha256,
                          attempt.record_mode,attempt.video_review_id is not null as review_bound,
                          attempt.video_authorization_id is not null as machine_authorization_bound,
                          attempt.object_version_id,
                          receipt.id as receipt_id,receipt.executed_at,receipt.estimated_cost,
                          receipt.cost_currency,result.final_summary
                   from project_las_analysis_attempt attempt
                   left join project_las_analysis_receipt receipt on receipt.attempt_id=attempt.id
                   left join project_las_analysis_result result on result.receipt_id=receipt.id
                   where attempt.project_id=%s and attempt.video_id=%s
                   order by attempt.created_at desc,attempt.id desc limit 20""",
                (project, video),
            ).fetchall()
        return {"project_id": str(project), "video_id": str(video),
                "runs": [{"attempt_id": str(row["id"]), "status": row["status"],
                          "error_code": row["error_code"],
                          "created_at": row["created_at"].isoformat(),
                          "provider_task_ref": row["provider_task_ref"],
                          "asset_sha256": row["asset_sha256"],
                          "record_mode": row["record_mode"],
                          "review_bound": row["review_bound"],
                          "machine_authorization_bound": row["machine_authorization_bound"],
                          "object_version_bound": row["object_version_id"] is not None,
                          "object_version_id": row["object_version_id"],
                          "receipt_id": str(row["receipt_id"]) if row["receipt_id"] else None,
                          "executed_at": row["executed_at"].isoformat() if row["executed_at"] else None,
                          "estimated_cost": (str(row["estimated_cost"])
                                             if row["estimated_cost"] is not None else None),
                          "cost_currency": row["cost_currency"],
                          "final_summary": row["final_summary"],
                          "provenance": "provider_machine_only"} for row in rows],
                "external_calls": 0}

    def approve(self, *, project_id: UUID | str, video_id: UUID | str,
                asset_id: UUID | str, review_version: str,
                expected_manifest_fingerprint: str, consent_statement: str,
                storage: PrivateS3MediaStorage,
                object_version_id: str) -> dict[str, object]:
        actor = self._human_actor()
        project, video, asset_key = (_uuid(project_id, "project_id"), _uuid(video_id, "video_id"),
                                     _uuid(asset_id, "asset_id"))
        if not isinstance(review_version, str) or not _VERSION.fullmatch(review_version):
            raise ValueError("LAS review version invalid")
        if not _is_versioned_review(review_version):
            raise ValueError("new LAS approval requires versioned review contract")
        _checked_version_id(object_version_id)
        if not isinstance(storage, PrivateS3MediaStorage):
            raise ValueError("trusted private storage required")
        if (not isinstance(consent_statement, str) or not 8 <= len(consent_statement.strip()) <= 2000):
            raise ValueError("explicit LAS consent statement required")
        if not isinstance(expected_manifest_fingerprint, str) or len(expected_manifest_fingerprint) != 64:
            raise ValueError("LAS expected manifest fingerprint required")
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            self._lock_video(conn, project, video)
            self._assert_manager(conn, project, actor)
            asset_before_read = self._asset(conn, video, asset_key)
        if storage.bucket != asset_before_read.bucket:
            raise PermissionError("LAS storage bucket mismatch")
        if storage.verified_version(_stored(asset_before_read), version_id=object_version_id) != object_version_id:
            raise ValueError("LAS video VersionId changed during verification")
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            self._assert_manager(conn, project, actor)
            existing = conn.execute(
                """select id,status,asset_manifest_fingerprint,review_statement,object_version_id
                   from project_las_video_review
                   where project_id=%s and asset_id=%s and review_version=%s for update""",
                (project, asset_key, review_version),
            ).fetchone()
            self._lock_video(conn, project, video)
            asset = self._asset(conn, video, asset_key)
            if asset != asset_before_read:
                raise ValueError("LAS video asset changed during byte approval")
            manifest = video_manifest(asset, self._delivery_origin, object_version_id)
            if manifest != expected_manifest_fingerprint:
                raise ValueError("video asset changed since LAS preview")
            row = existing or conn.execute(
                """insert into project_las_video_review(project_id,video_id,asset_id,
                     asset_sha256,asset_manifest_fingerprint,delivery_origin,review_version,
                     review_statement,binding_scheme,object_version_id)
                   values(%s,%s,%s,%s,%s,%s,%s,%s,'versioned_bytes_v2',%s)
                   on conflict(project_id,asset_id,review_version) do nothing returning id""",
                (project, video, asset_key, asset.content_sha256, manifest,
                 self._delivery_origin, review_version,
                 Jsonb({"consent_statement": consent_statement.strip()}), object_version_id),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    """select id,status,asset_manifest_fingerprint,review_statement,object_version_id
                       from project_las_video_review
                       where project_id=%s and asset_id=%s and review_version=%s for update""",
                    (project, asset_key, review_version),
                ).fetchone()
            if row is None:
                raise ValueError("LAS review version could not be resolved")
            if "status" in row and (row["status"] == "revoked" or
                                    row["asset_manifest_fingerprint"] != manifest or
                                    row["object_version_id"] != object_version_id or
                                    row["review_statement"] !=
                                    {"consent_statement": consent_statement.strip()}):
                raise ValueError("LAS review version conflicts with immutable prior review")
            review_id = row["id"]
            conn.execute(
                """update project_las_video_review set status='approved',reviewed_by=%s
                   where id=%s and status='draft'""", (actor, review_id),
            )
            current = conn.execute(
                "select status from project_las_video_review where id=%s", (review_id,),
            ).fetchone()
            if current is None or current["status"] != "approved":
                raise ValueError("LAS review not approvable")
        return {"review_id": str(review_id), "status": "approved", "external_calls": 0}

    def authorize_machine_first(self, *, project_id: UUID | str, video_id: UUID | str,
                                asset_id: UUID | str, expected_manifest_fingerprint: str,
                                consent_statement: str, storage: PrivateS3MediaStorage,
                                object_version_id: str) -> dict[str, object]:
        """Permit one versioned video transfer without claiming anyone reviewed it."""
        actor = self._human_actor()
        project, video, asset_key = (_uuid(project_id, "project_id"), _uuid(video_id, "video_id"),
                                     _uuid(asset_id, "asset_id"))
        _checked_version_id(object_version_id)
        if not isinstance(storage, PrivateS3MediaStorage):
            raise ValueError("trusted private storage required")
        if (not isinstance(consent_statement, str) or
                not 8 <= len(consent_statement.strip()) <= 2000):
            raise ValueError("explicit machine-first LAS consent required")
        if not isinstance(expected_manifest_fingerprint, str) or not re.fullmatch(
            r"[0-9a-f]{64}", expected_manifest_fingerprint
        ):
            raise ValueError("LAS expected manifest fingerprint required")
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            self._lock_project(conn, project)
            self._lock_video(conn, project, video)
            self._assert_manager(conn, project, actor)
            asset_before_read = self._asset(conn, video, asset_key)
        if storage.bucket != asset_before_read.bucket:
            raise PermissionError("LAS storage bucket mismatch")
        if storage.verified_version(_stored(asset_before_read),
                                    version_id=object_version_id) != object_version_id:
            raise ValueError("LAS video VersionId changed during verification")
        statement = {"cloud_consent": consent_statement.strip(),
                     "human_review_status": "not_asserted"}
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            self._lock_project(conn, project)
            self._lock_video(conn, project, video)
            self._assert_manager(conn, project, actor)
            asset = self._asset(conn, video, asset_key)
            existing = conn.execute(
                """select id,status,asset_manifest_fingerprint,consent_statement,
                          object_version_id from project_las_video_authorization
                   where project_id=%s and asset_id=%s and authorization_version=%s
                     and status='authorized'
                   for update""", (project, asset_key, _MACHINE_AUTHORIZATION),
            ).fetchone()
            if asset != asset_before_read:
                raise ValueError("LAS video asset changed during authorization")
            manifest = video_manifest(asset, self._delivery_origin, object_version_id)
            if manifest != expected_manifest_fingerprint:
                raise ValueError("video asset changed since LAS preview")
            row = existing or conn.execute(
                """insert into project_las_video_authorization(
                     project_id,video_id,asset_id,asset_sha256,
                     asset_manifest_fingerprint,delivery_origin,object_version_id,
                     authorization_version,consent_statement)
                   values(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   returning id""",
                (project, video, asset_key, asset.content_sha256, manifest,
                 self._delivery_origin, object_version_id, _MACHINE_AUTHORIZATION,
                 Jsonb(statement)),
            ).fetchone()
            if existing and (row["asset_manifest_fingerprint"] != manifest or
                             row["object_version_id"] != object_version_id or
                             row["consent_statement"] != statement):
                raise ValueError("revoke prior LAS authorization before binding new bytes")
            authorization_id = row["id"]
            conn.execute(
                """update project_las_video_authorization
                      set status='authorized',authorized_by=%s
                    where id=%s and status='draft'""", (actor, authorization_id),
            )
            current = conn.execute(
                "select status from project_las_video_authorization where id=%s",
                (authorization_id,),
            ).fetchone()
            if current is None or current["status"] != "authorized":
                raise ValueError("LAS authorization not active")
        return {"authorization_id": str(authorization_id), "status": "authorized",
                "human_review_status": "not_asserted", "external_calls": 0}

    def revoke_machine_authorization(self, *, project_id: UUID | str,
                                     authorization_id: UUID | str) -> dict[str, object]:
        actor = self._human_actor()
        project, authorization = (_uuid(project_id, "project_id"),
                                  _uuid(authorization_id, "authorization_id"))
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            self._lock_project(conn, project)
            self._assert_manager(conn, project, actor)
            conn.execute(
                """select id from project_las_analysis_attempt
                   where video_authorization_id=%s and status='prepared'
                   order by id for update""", (authorization,),
            ).fetchall()
            row = conn.execute(
                """update project_las_video_authorization
                      set status='revoked',revoked_by=%s
                    where id=%s and project_id=%s and status in ('draft','authorized')
                    returning id""", (actor, authorization, project),
            ).fetchone()
            if row is None:
                raise ValueError("LAS authorization unavailable or already revoked")
            conn.execute(
                """update project_las_analysis_attempt
                      set status='cancelled',error_code='authorization_revoked_before_dispatch'
                    where video_authorization_id=%s and status='prepared'""",
                (authorization,),
            )
        return {"authorization_id": str(authorization), "status": "revoked",
                "external_calls": 0}

    def revoke(self, *, project_id: UUID | str, review_id: UUID | str) -> dict[str, object]:
        actor = self._human_actor()
        project, review = _uuid(project_id, "project_id"), _uuid(review_id, "review_id")
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            self._assert_manager(conn, project, actor)
            row = conn.execute(
                """update project_las_video_review set status='revoked',revoked_by=%s
                   where id=%s and project_id=%s and status in ('draft','approved')
                   returning id""", (actor, review, project),
            ).fetchone()
            if row is None:
                raise ValueError("LAS review unavailable or already revoked")
            conn.execute(
                """update project_las_analysis_attempt
                      set status='cancelled',error_code='review_revoked_before_dispatch'
                    where video_review_id=%s and status='prepared'""",
                (review,),
            )
        return {"review_id": str(review), "status": "revoked", "external_calls": 0}

    def prepare(self, *, project_id: UUID | str, video_id: UUID | str,
                review_id: UUID | str) -> dict[str, object]:
        """Owner/admin's explicit paid request; one attempt per review version."""
        actor = self._human_actor()
        project, video, review_key = (_uuid(project_id, "project_id"),
                                      _uuid(video_id, "video_id"), _uuid(review_id, "review_id"))
        if not self._account_scope:
            raise ValueError("server LAS account scope required")
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            self._assert_manager(conn, project, actor)
            review = conn.execute(
                """select * from project_las_video_review
                   where id=%s and project_id=%s and video_id=%s and status='approved'
                   for update""", (review_key, project, video),
            ).fetchone()
            if review is None:
                raise PermissionError("approved project LAS video review required")
            if (review["binding_scheme"] != "versioned_bytes_v2" or
                    not review["object_version_id"]):
                raise PermissionError("legacy LAS review cannot authorize a new paid request")
            self._lock_video(conn, project, video)
            asset = self._asset(conn, video, review["asset_id"])
            if (asset.content_sha256 != review["asset_sha256"] or
                    video_manifest(asset, self._delivery_origin, review["object_version_id"]) !=
                    review["asset_manifest_fingerprint"] or
                    review["delivery_origin"] != self._delivery_origin):
                raise PermissionError("LAS review no longer binds current video asset")
            identity = (str(project), str(video), str(review_key), str(asset.id),
                        asset.content_sha256, review["object_version_id"], LAS_OPERATOR_ID,
                        LAS_TEMPLATE, LAS_MODEL_ID)
            fingerprint = hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
            task_key = f"project-las-v2:{fingerprint}"
            row = conn.execute(
                """insert into project_las_analysis_attempt(project_id,video_id,asset_id,
                     asset_sha256,task_key,attempt_no,provider,account_scope,cost_currency,
                     status,authorization_ref,authorized_by,record_mode,input_fingerprint,
                     video_review_id,requested_by,object_version_id)
                   values(%s,%s,%s,%s,%s,1,%s,%s,'CNY','prepared',%s,%s,
                          'live_pre_dispatch',%s,%s,%s,%s)
                   on conflict(task_key,attempt_no) do nothing returning id,status,account_scope""",
                (project, video, asset.id, asset.content_sha256, task_key, _PROVIDER,
                 self._account_scope, str(review_key), review["reviewed_by"], fingerprint,
                 review_key, actor, review["object_version_id"]),
            ).fetchone()
            created = row is not None
            if row is None:
                row = conn.execute(
                    """select id,status,account_scope from project_las_analysis_attempt
                       where task_key=%s and attempt_no=1""", (task_key,),
                ).fetchone()
            if row is None:
                raise RuntimeError("LAS attempt identity could not be resolved")
            if row["account_scope"] != self._account_scope:
                raise PermissionError("existing LAS request belongs to a different account scope")
        return {"attempt_id": str(row["id"]), "status": row["status"],
                "created": created, "external_calls": 0}

    def prepare_machine_first(self, *, project_id: UUID | str, video_id: UUID | str,
                              authorization_id: UUID | str) -> dict[str, object]:
        """Owner/admin requests paid machine evidence; human review stays absent."""
        actor = self._human_actor()
        project, video, permission_key = (_uuid(project_id, "project_id"),
                                          _uuid(video_id, "video_id"),
                                          _uuid(authorization_id, "authorization_id"))
        if not self._account_scope:
            raise ValueError("server LAS account scope required")
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            self._lock_project(conn, project)
            self._lock_video(conn, project, video)
            self._assert_manager(conn, project, actor)
            permission_row = conn.execute(
                """select * from project_las_video_authorization
                   where id=%s and project_id=%s and video_id=%s
                     and status='authorized' for update""",
                (permission_key, project, video),
            ).fetchone()
            if permission_row is None:
                raise PermissionError("active project LAS cloud authorization required")
            asset = self._asset(conn, video, permission_row["asset_id"])
            version_id = permission_row["object_version_id"]
            if (asset.content_sha256 != permission_row["asset_sha256"] or
                    video_manifest(asset, self._delivery_origin, version_id) !=
                    permission_row["asset_manifest_fingerprint"] or
                    permission_row["delivery_origin"] != self._delivery_origin):
                raise PermissionError("LAS authorization no longer binds current video asset")
            identity = (str(project), str(video), str(permission_key), str(asset.id),
                        asset.content_sha256, version_id, LAS_OPERATOR_ID,
                        LAS_TEMPLATE, LAS_MODEL_ID)
            fingerprint = hashlib.sha256(json.dumps(
                identity, separators=(",", ":")
            ).encode()).hexdigest()
            task_key = f"project-las-machine-v1:{fingerprint}"
            row = conn.execute(
                """insert into project_las_analysis_attempt(
                     project_id,video_id,asset_id,asset_sha256,task_key,attempt_no,
                     provider,account_scope,cost_currency,status,authorization_ref,
                     authorized_by,record_mode,input_fingerprint,video_authorization_id,
                     requested_by,object_version_id)
                   values(%s,%s,%s,%s,%s,1,%s,%s,'CNY','prepared',%s,%s,
                          'live_pre_dispatch',%s,%s,%s,%s)
                   on conflict(task_key,attempt_no) do nothing
                   returning id,status,account_scope""",
                (project, video, asset.id, asset.content_sha256, task_key, _PROVIDER,
                 self._account_scope, str(permission_key), permission_row["authorized_by"],
                 fingerprint, permission_key, actor, version_id),
            ).fetchone()
            created = row is not None
            if row is None:
                row = conn.execute(
                    """select id,status,account_scope from project_las_analysis_attempt
                       where task_key=%s and attempt_no=1""", (task_key,),
                ).fetchone()
            if row is None:
                raise RuntimeError("LAS attempt identity could not be resolved")
            if row["account_scope"] != self._account_scope:
                raise PermissionError("existing LAS request belongs to another account scope")
        return {"attempt_id": str(row["id"]), "status": row["status"],
                "created": created, "human_review_status": "not_asserted",
                "external_calls": 0}

    def _require_worker(self, *, needs_storage: bool = True) -> str:
        if (not self._worker_actor or not self._account_scope or
                (needs_storage and (not self._storage_location or not self._bucket))):
            raise PermissionError("trusted LAS worker configuration required")
        return self._worker_actor

    def dispatch(self, *, attempt_id: UUID | str, storage: PrivateS3MediaStorage,
                 provider: LASClient) -> dict[str, object]:
        """Claim and COMMIT once, then send at most one provider Submit."""
        self._require_worker()
        attempt_key = _uuid(attempt_id, "attempt_id")
        if getattr(provider, "max_retries", None) != 0 or not isinstance(storage, PrivateS3MediaStorage):
            raise ValueError("trusted zero-retry LAS provider and private storage required")
        if storage.bucket != self._bucket:
            raise PermissionError("LAS storage bucket mismatch")
        # Read all video bytes outside a database row lock. The immutable
        # VersionId, not the mutable latest Key, is what will be signed later.
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            preflight = conn.execute(
                """select * from project_las_analysis_attempt
                   where id=%s and record_mode='live_pre_dispatch'""", (attempt_key,),
            ).fetchone()
            if preflight is None:
                raise ValueError("live LAS attempt unavailable")
            if preflight["status"] != "prepared":
                return {"attempt_id": str(attempt_key), "status": preflight["status"],
                        "external_calls": 0, "reused": True}
            if preflight["account_scope"] != self._account_scope:
                raise PermissionError("LAS account scope mismatch")
            version_id = _checked_version_id(preflight["object_version_id"])
            preflight_asset = self._asset(conn, preflight["video_id"], preflight["asset_id"])
            if (preflight_asset.storage_location != self._storage_location or
                    preflight_asset.bucket != self._bucket):
                raise PermissionError("LAS storage location mismatch")
        storage.verified_version(_stored(preflight_asset), version_id=version_id)
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            self._lock_project(conn, preflight["project_id"])
            self._lock_video(conn, preflight["project_id"], preflight["video_id"])
            attempt = conn.execute(
                """select * from project_las_analysis_attempt
                   where id=%s and record_mode='live_pre_dispatch' for update""",
                (attempt_key,),
            ).fetchone()
            if attempt is None:
                raise ValueError("live LAS attempt unavailable")
            if attempt["status"] != "prepared":
                return {"attempt_id": str(attempt_key), "status": attempt["status"],
                        "external_calls": 0, "reused": True}
            if attempt["account_scope"] != self._account_scope:
                raise PermissionError("LAS account scope mismatch")
            machine_first = attempt["video_authorization_id"] is not None
            if machine_first:
                permission_row = conn.execute(
                    """select * from project_las_video_authorization
                       where id=%s and project_id=%s and video_id=%s and asset_id=%s
                         and status='authorized' for update""",
                    (attempt["video_authorization_id"], attempt["project_id"],
                     attempt["video_id"], attempt["asset_id"]),
                ).fetchone()
                permission_version = _MACHINE_AUTHORIZATION
            else:
                permission_row = conn.execute(
                    """select * from project_las_video_review
                       where id=%s and project_id=%s and video_id=%s and asset_id=%s
                         and status='approved' for update""",
                    (attempt["video_review_id"], attempt["project_id"],
                     attempt["video_id"], attempt["asset_id"]),
                ).fetchone()
                permission_version = permission_row["review_version"] if permission_row else ""
            if permission_row is None:
                raise PermissionError("LAS permission was revoked before dispatch")
            if ((not machine_first and permission_row["binding_scheme"] != "versioned_bytes_v2") or
                    permission_row["object_version_id"] != version_id or
                    attempt["object_version_id"] != version_id):
                raise PermissionError("LAS permitted byte version changed")
            asset = self._asset(conn, attempt["video_id"], attempt["asset_id"])
            if (asset.storage_location != self._storage_location or asset.bucket != self._bucket or
                    asset.content_sha256 != attempt["asset_sha256"] or
                    permission_row["asset_sha256"] != asset.content_sha256 or
                    permission_row["delivery_origin"] != self._delivery_origin or
                    permission_row["asset_manifest_fingerprint"] !=
                    video_manifest(asset, self._delivery_origin, version_id) or
                    asset != preflight_asset):
                raise PermissionError("LAS permitted private object changed")
            url = storage.presigned_read_url(asset.object_key, expires_in=3600,
                                            version_id=version_id)
            parsed = urlsplit(url)
            if _origin(f"{parsed.scheme}://{parsed.netloc}") != self._delivery_origin or parsed.fragment:
                raise PermissionError("LAS signed URL origin mismatch")
            delivery = ReviewedLASVideoDelivery(
                url=url, asset_sha256=asset.content_sha256,
                review_version=permission_version,
                query_sha256=(hashlib.sha256(parsed.query.encode()).hexdigest()
                              if parsed.query else None),
            )
            conn.execute(
                """update project_las_analysis_attempt
                      set status='submitting',submission_count=1
                    where id=%s and status='prepared' and submission_count=0""",
                (attempt_key,),
            )
        # The claim is now durable. Re-lock before HTTP so a revocation or
        # takedown that committed in the gap can stop the call; a concurrent
        # revocation waits until the Submit finishes.
        submit_ref = str(uuid4())
        try:
            with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
                self._lock_project(conn, preflight["project_id"])
                claimed = conn.execute(
                    """select * from project_las_analysis_attempt
                       where id=%s and status='submitting' and submission_count=1 for update""",
                    (attempt_key,),
                ).fetchone()
                if claimed is None:
                    raise RuntimeError("durable LAS claim changed before HTTP")
                if claimed["video_authorization_id"] is not None:
                    current_permission = conn.execute(
                        """select * from project_las_video_authorization
                           where id=%s and status='authorized' for update""",
                        (claimed["video_authorization_id"],),
                    ).fetchone()
                else:
                    current_permission = conn.execute(
                        """select * from project_las_video_review
                           where id=%s and status='approved' for update""",
                        (claimed["video_review_id"],),
                    ).fetchone()
                if current_permission is None:
                    conn.execute(
                        """update project_las_analysis_attempt
                              set status='failed',error_code='permission_revoked_before_http'
                            where id=%s""", (attempt_key,),
                    )
                    return {"attempt_id": str(attempt_key), "status": "failed",
                            "external_calls": 0, "reused": False}
                try:
                    self._lock_video(conn, claimed["project_id"], claimed["video_id"])
                    current_asset = self._asset(conn, claimed["video_id"], claimed["asset_id"])
                    if (current_asset.content_sha256 != claimed["asset_sha256"] or
                            claimed["object_version_id"] != version_id or
                            current_permission["object_version_id"] != version_id or
                            video_manifest(current_asset, self._delivery_origin, version_id) !=
                            current_permission["asset_manifest_fingerprint"]):
                        raise PermissionError("permitted LAS asset changed before HTTP")
                except (PermissionError, ValueError):
                    conn.execute(
                        """update project_las_analysis_attempt
                              set status='failed',error_code='source_changed_before_http'
                            where id=%s""", (attempt_key,),
                    )
                    return {"attempt_id": str(attempt_key), "status": "failed",
                            "external_calls": 0, "reused": False}
                # Any provider exception, including malformed 200 JSON, is
                # post-Submit uncertainty, never permission to resubmit.
                try:
                    submission = provider.submit(delivery)
                except Exception:
                    conn.execute(
                        """update project_las_analysis_attempt
                              set status='unknown',error_code='submit_outcome_unknown'
                            where id=%s""", (attempt_key,),
                    )
                    return {"attempt_id": str(attempt_key), "status": "unknown",
                            "external_calls": 1, "reused": False}
                conn.execute(
                    """update project_las_analysis_attempt
                          set status='submitted',provider_task_ref=%s,submit_job_ref=%s
                        where id=%s and status='submitting' and submission_count=1""",
                    (submission.task_id, submit_ref, attempt_key),
                )
        except Exception:
            # A durable submitting row still blocks replay even if the second
            # transaction could not commit a valid provider task reference.
            raise RuntimeError("claimed LAS attempt unresolved; never resubmit") from None
        return {"attempt_id": str(attempt_key), "status": "submitted",
                "provider_task_ref": submission.task_id, "external_calls": 1,
                "reused": False}

    def _mark_unknown(self, attempt_id: UUID, error_code: str) -> None:
        try:
            with psycopg.connect(self._dsn) as conn:
                conn.execute(
                    """update project_las_analysis_attempt
                          set status='unknown',error_code=%s
                        where id=%s and status in ('submitting','submitted','running','unknown')""",
                    (error_code, attempt_id),
                )
        except Exception:
            # The durable submitting/submitted row still blocks any replay.
            pass

    def poll(self, *, attempt_id: UUID | str, provider: LASClient) -> dict[str, object]:
        worker = self._require_worker(needs_storage=False)
        attempt_key = _uuid(attempt_id, "attempt_id")
        if getattr(provider, "max_retries", None) != 0:
            raise ValueError("zero-retry LAS provider required")
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            attempt = conn.execute(
                """select * from project_las_analysis_attempt
                   where id=%s and record_mode='live_pre_dispatch'""",
                (attempt_key,),
            ).fetchone()
        if attempt is None:
            raise ValueError("live LAS attempt unavailable")
        if attempt["account_scope"] != self._account_scope:
            raise PermissionError("LAS account scope mismatch")
        if attempt["status"] in {"completed", "failed", "cancelled"}:
            return {"attempt_id": str(attempt_key), "status": attempt["status"],
                    "external_calls": 0, "reused": True}
        if not attempt["provider_task_ref"] or not attempt["submit_job_ref"]:
            return {"attempt_id": str(attempt_key), "status": attempt["status"],
                    "external_calls": 0, "needs_manual_reconciliation": True}
        if attempt["provider_task_recorded_at"] is None:
            return {"attempt_id": str(attempt_key), "status": attempt["status"],
                    "external_calls": 0, "needs_manual_reconciliation": True}
        if datetime.now(timezone.utc) - attempt["provider_task_recorded_at"] > timedelta(hours=70):
            self._mark_unknown(attempt_key, "provider_task_expiry_near")
            return {"attempt_id": str(attempt_key), "status": "unknown",
                    "external_calls": 0, "needs_manual_reconciliation": True}
        try:
            observed = provider.poll(attempt["provider_task_ref"])
        except Exception:
            self._mark_unknown(attempt_key, "poll_outcome_unknown")
            return {"attempt_id": str(attempt_key), "status": "unknown", "external_calls": 1}
        if observed.task_id != attempt["provider_task_ref"] or observed.task_status not in {
            "PENDING", "RUNNING", "COMPLETED", "FAILED", "TIMEOUT"
        }:
            self._mark_unknown(attempt_key, "poll_binding_invalid")
            return {"attempt_id": str(attempt_key), "status": "unknown", "external_calls": 1}
        if observed.task_status in {"PENDING", "RUNNING"}:
            if observed.business_code != "0":
                self._mark_unknown(attempt_key, "poll_business_code_nonzero")
                return {"attempt_id": str(attempt_key), "status": "unknown", "external_calls": 1}
            with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
                current = conn.execute(
                    "select status from project_las_analysis_attempt where id=%s for update",
                    (attempt_key,),
                ).fetchone()
                if current is None:
                    raise RuntimeError("LAS attempt disappeared during Poll")
                if current["status"] in {"completed", "failed", "cancelled"}:
                    return {"attempt_id": str(attempt_key), "status": current["status"],
                            "external_calls": 1, "reused": True}
                next_status = ("running" if observed.task_status == "RUNNING" or
                               current["status"] == "running" else "submitted")
                conn.execute(
                    "update project_las_analysis_attempt set status=%s where id=%s",
                    (next_status, attempt_key),
                )
            return {"attempt_id": str(attempt_key), "status": next_status,
                    "external_calls": 1}
        status = "completed" if observed.task_status == "COMPLETED" else "failed"
        if status == "completed" and (not observed.token_usages or any(
            usage.get("model_name") != LAS_MODEL_ID or
            not isinstance(usage.get("token_usage"), dict) or
            not usage["token_usage"] or
            any(type(count) is not int or count < 0 for count in usage["token_usage"].values())
            for usage in observed.token_usages
        )):
            self._mark_unknown(attempt_key, "completion_model_usage_mismatch")
            return {"attempt_id": str(attempt_key), "status": "unknown",
                    "external_calls": 1, "needs_manual_reconciliation": True}
        if observed.end_time is None:
            self._mark_unknown(attempt_key, "terminal_time_missing")
            return {"attempt_id": str(attempt_key), "status": "unknown", "external_calls": 1}
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            current = conn.execute(
                "select * from project_las_analysis_attempt where id=%s for update",
                (attempt_key,),
            ).fetchone()
            if current is None or current["provider_task_ref"] != observed.task_id:
                raise RuntimeError("LAS provider task binding changed")
            if current["status"] in {"completed", "failed", "cancelled"}:
                return {"attempt_id": str(attempt_key), "status": current["status"],
                        "external_calls": 1, "reused": True}
            conn.execute(
                """update project_las_analysis_attempt set status=%s,error_code=%s
                   where id=%s""",
                (status, None if status == "completed" else observed.business_code[:160],
                 attempt_key),
            )
            receipt_id = conn.execute(
                """insert into project_las_analysis_receipt(
                     attempt_id,project_id,video_id,asset_id,asset_sha256,task_key,attempt_no,
                     provider,account_scope,provider_task_ref,submit_job_ref,completion_job_ref,
                     model_id,operator_id,operator_version,template_id,status,business_code,
                     result_sha256,token_usages,cost_currency,executed_at,recorded_by,
                     binding_basis,object_version_id)
                   values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                          %s,%s,%s,%s,%s) returning id""",
                (attempt_key, current["project_id"], current["video_id"],
                 current["asset_id"], current["asset_sha256"], current["task_key"],
                 current["attempt_no"], current["provider"], current["account_scope"],
                 current["provider_task_ref"], current["submit_job_ref"], str(uuid4()),
                 LAS_MODEL_ID, LAS_OPERATOR_ID, LAS_OPERATOR_VERSION, LAS_TEMPLATE, status,
                 0 if status == "completed" else None, observed.response_sha256,
                 Jsonb({"items": list(observed.token_usages)} if status == "completed" else {}),
                 current["cost_currency"], datetime.fromisoformat(observed.end_time), worker,
                 ("authorized_machine_first_and_result" if
                  current["video_authorization_id"] is not None else
                  "reviewed_submission_and_result"), current["object_version_id"]),
            ).fetchone()["id"]
            if status == "completed":
                if not observed.final_summary:
                    raise RuntimeError("LAS completed result has no summary")
                summary_hash = hashlib.sha256(observed.final_summary.encode()).hexdigest()
                conn.execute(
                    """insert into project_las_analysis_result(receipt_id,project_id,video_id,
                         final_summary,summary_sha256)
                       values(%s,%s,%s,%s,%s)""",
                    (receipt_id, current["project_id"], current["video_id"],
                     observed.final_summary, summary_hash),
                )
        return {"attempt_id": str(attempt_key), "status": status,
                "receipt_id": str(receipt_id), "external_calls": 1,
                "cost_basis": "unknown_until_supplier_bill"}
