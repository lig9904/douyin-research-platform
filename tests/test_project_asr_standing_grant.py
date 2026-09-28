from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from douyin_research.project_analysis.asr_backend import (
    ProjectASRDispatch, ProjectASRService, _public_douyin_video,
)


ROOT = Path(__file__).resolve().parents[1]
DSN = os.getenv("TEST_DATABASE_URL")
SCHEMA = ROOT / "db/schema.sql"
MIGRATION = ROOT / "db/migrations/038_project_asr_standing_grant.sql"
PROVIDER = "volcengine-doubao-asr"
SCOPE = "accepted_available_public_video"
FINGERPRINT = "a" * 64
MANIFEST = "b" * 64


def test_migration_038_and_schema_expose_standing_grant_contract() -> None:
    migration = MIGRATION.read_text(encoding="utf-8")
    schema = SCHEMA.read_text(encoding="utf-8")
    for definition in (
        "create table if not exists project_asr_standing_grant",
        "trg_project_asr_standing_grant_lifecycle",
        "project_asr_media_review_authorization_state_check",
        "standing ASR authorization does not assert human listening",
        "requires matching active standing grant for new submission",
    ):
        assert definition in migration
        assert definition in schema
    assert "reviewed_by is null and reviewed_at is null" in migration
    assert "new_submission and grant_row.status <> 'active'" in migration
    assert "old.status not in ('submitting','submitted','running')" in migration


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"platform": "douyin", "platform_video_id": "1234567890", "source_url": "https://www.douyin.com/video/1234567890"}, True),
        ({"platform": "douyin", "platform_video_id": "1234567890", "source_url": "https://www.iesdouyin.com/share/video/1234567890/?region=US"}, True),
        ({"platform": "douyin", "platform_video_id": "1234567890", "source_url": "https://www.iesdouyin.com/share/video/9999999999/?region=US"}, False),
        ({"platform": "tiktok", "platform_video_id": "1234567890", "source_url": "https://www.douyin.com/video/1234567890"}, False),
        ({"platform": "douyin", "platform_video_id": "1234567890", "source_url": "https://douyin.com/video/9999999999"}, False),
        ({"platform": "douyin", "platform_video_id": "1234567890", "source_url": "http://www.douyin.com/video/1234567890"}, False),
        ({"platform": "douyin", "platform_video_id": "1234567890", "source_url": "https://evil.example/video/1234567890"}, False),
        ({"platform": "douyin", "platform_video_id": "1234567890", "source_url": "https://www.douyin.com/video/1234567890/extra"}, False),
        ({"platform": "douyin", "platform_video_id": "1234567890", "source_url": "https://user@www.douyin.com/video/1234567890"}, False),
        ({"platform": "douyin", "platform_video_id": "bad/id", "source_url": "https://www.douyin.com/video/bad/id"}, False),
    ],
)
def test_public_douyin_video_validator(row: dict[str, str], expected: bool) -> None:
    assert _public_douyin_video(row) is expected


@pytest.mark.skipif(not DSN, reason="isolated TEST_DATABASE_URL required")
def test_migration_038_upgrades_037_schema_and_is_replay_safe() -> None:
    assert DSN
    namespace = f"standing_upgrade_{uuid4().hex}"
    marker = "-- A project may authorize cloud ASR for its accepted, available public-video"
    baseline, separator, _ = SCHEMA.read_text(encoding="utf-8").partition(marker)
    assert separator
    migration = MIGRATION.read_text(encoding="utf-8")
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}, public").format(sql.Identifier(namespace)))
            conn.execute(baseline, prepare=False)
            for _ in range(2):
                conn.execute(migration, prepare=False)
            assert conn.execute("select to_regclass('project_asr_standing_grant') is not null").fetchone()[0]
            assert conn.execute("""select count(*) from pg_constraint
                where conrelid='project_asr_media_review'::regclass
                  and conname in ('project_asr_media_review_standing_grant_fk',
                                  'project_asr_media_review_authorization_state_check')""").fetchone()[0] == 2
        finally:
            conn.execute("set search_path to public")
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))


@pytest.mark.skipif(not DSN, reason="isolated TEST_DATABASE_URL required")
def test_standing_grant_review_and_asr_job_lifecycle() -> None:
    assert DSN
    namespace = f"standing_asr_{uuid4().hex}"
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}, public").format(sql.Identifier(namespace)))
            conn.execute(SCHEMA.read_text(encoding="utf-8"), prepare=False)

            org_id = conn.execute(
                "insert into research_organization(slug,name) values (%s,%s) returning id",
                ("standing-test", "standing test"),
            ).fetchone()[0]
            project_a, project_b = [
                conn.execute(
                    """insert into research_project(organization_id,slug,name,status)
                       values (%s,%s,%s,'active') returning id""",
                    (org_id, f"standing-{suffix}", suffix),
                ).fetchone()[0]
                for suffix in ("a", "b")
            ]
            video_a, video_b = [
                conn.execute(
                    """insert into source_video(platform,platform_video_id,title,source_url)
                       values ('douyin',%s,'standing video',%s) returning id""",
                    (f"123456789{index}", f"https://www.douyin.com/video/123456789{index}"),
                ).fetchone()[0]
                for index in (0, 1)
            ]
            conn.execute(
                """insert into project_video_inclusion(project_id,video_id,source_type,status) values
                   (%s,%s,'manual','accepted'),(%s,%s,'manual','accepted')""",
                (project_a, video_a, project_b, video_b),
            )
            grant_id = conn.execute(
                """insert into project_asr_standing_grant(project_id,provider,source_scope,authorized_by)
                   values (%s,%s,%s,'owner@example.com') returning id""",
                (project_a, PROVIDER, SCOPE),
            ).fetchone()[0]

            def make_review(project_id, video_id):
                asset_id = conn.execute(
                    """insert into media_asset(video_id,kind,storage_location,bucket,object_key,
                           content_sha256,size_bytes,content_type)
                       values (%s,'audio','s3','test',%s,%s,64,'audio/wav') returning id""",
                        (video_id, f"sha256/{FINGERPRINT[:2]}/{FINGERPRINT}", FINGERPRINT),
                ).fetchone()[0]
                return conn.execute(
                    """insert into project_asr_media_review(project_id,video_id,asset_id,review_version,
                           media_fingerprint,asset_manifest_fingerprint,delivery_origin,identity_source,
                           review_statement,authorization_kind,standing_grant_id)
                       values (%s,%s,%s,%s,%s,%s,'https://media.example.test',
                           'windmill_end_user_email_allowlist_v1',
                           '{"listening_status":"not_listened","consent_basis":"project_standing_grant"}',
                           'standing_grant',%s) returning id""",
                    (project_id, video_id, asset_id, f"standing-media-v1:{grant_id}",
                     FINGERPRINT, MANIFEST, grant_id),
                ).fetchone()[0], asset_id

            review_a, asset_a = make_review(project_a, video_a)
            conn.execute("update project_asr_media_review set status='approved' where id=%s", (review_a,))
            review = conn.execute(
                "select status,authorization_kind,reviewed_by,reviewed_at,review_statement from project_asr_media_review where id=%s",
                (review_a,),
            ).fetchone()
            assert review[0:4] == ("approved", "standing_grant", None, None)
            assert review[4]["listening_status"] == "not_listened"

            def add_job(task_key: str, *, project_id=project_a, video_id=video_a,
                        media_review_id=review_a, reviewed_asset_id=asset_a,
                        provider=PROVIDER, status="submitting", provider_task_ref=None):
                return conn.execute(
                    """insert into project_asr_execution_job(task_key,project_id,video_id,media_review_id,
                           reviewed_asset_id,review_version,media_fingerprint,asset_manifest_fingerprint,
                           provider,model_id,model_revision,engine_version,source_fingerprint,status,
                           provider_task_ref,submission_count,cost_currency)
                       values (%s,%s,%s,%s,%s,%s,%s,%s,%s,'test-model','v1','wav-v1',%s,
                           %s,%s,case when %s in ('submitted','running','completed') then 1 else 0 end,'USD')
                       returning id""",
                    (task_key, project_id, video_id, media_review_id, reviewed_asset_id,
                     f"standing-media-v1:{grant_id}", FINGERPRINT, MANIFEST, provider,
                     FINGERPRINT, status, provider_task_ref, status),
                ).fetchone()[0]

            job_id = add_job("standing-submitted", status="submitted", provider_task_ref="provider-task")
            with pytest.raises(psycopg.Error, match="matching active standing grant"):
                add_job("standing-provider-mismatch", provider="other-provider")
            with pytest.raises(psycopg.Error):
                add_job("standing-cross-project", project_id=project_b, video_id=video_b)

            with pytest.raises(psycopg.errors.UniqueViolation):
                add_job("standing-submitted", status="submitted", provider_task_ref="duplicate")

            # An HTTP submit may have started before either authorization was
            # revoked. Its outcome is ambiguous, so persist the unknown cost
            # fact while keeping the job submitting and never reopening submit.
            ambiguous_job = add_job("standing-ambiguous", status="submitting")

            conn.execute(
                "update project_asr_standing_grant set status='revoked',revoked_by='owner@example.com' where id=%s",
                (grant_id,),
            )
            with pytest.raises(psycopg.Error, match="matching active standing grant"):
                add_job("standing-after-revoke")
            conn.execute("update project_asr_execution_job set status='running' where id=%s", (job_id,))
            conn.execute("update project_asr_execution_job set status='completed' where id=%s", (job_id,))
            assert conn.execute("select status from project_asr_execution_job where id=%s", (job_id,)).fetchone()[0] == "completed"
            conn.execute(
                "update project_asr_media_review set status='revoked',revoked_by='owner@example.com' where id=%s",
                (review_a,),
            )

            cost_id = conn.execute(
                """insert into project_research_task_cost(project_id,video_id,task_key,task_type,
                       task_version,status,input_fingerprint,cost_currency,cost_basis)
                   values (%s,%s,'standing-ambiguous','asr_transcription','test-v1','failed',%s,'USD','unknown')
                   returning id""",
                (project_a, video_a, FINGERPRINT),
            ).fetchone()[0]
            conn.execute(
                """update project_asr_execution_job set submission_count=1,task_cost_id=%s,
                       error_code='project_asr_reconciliation_required' where id=%s""",
                (cost_id, ambiguous_job),
            )
            assert conn.execute(
                """select status,submission_count,error_code,provider_task_ref,task_cost_id
                   from project_asr_execution_job where id=%s""",
                (ambiguous_job,),
            ).fetchone() == (
                "submitting", 1, "project_asr_reconciliation_required", None, cost_id,
            )
        finally:
            conn.execute("set search_path to public")
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))


@pytest.mark.skipif(not DSN, reason="isolated TEST_DATABASE_URL required")
def test_project_service_records_standing_authorization_without_listening(monkeypatch) -> None:
    assert DSN
    namespace = f"standing_service_{uuid4().hex}"
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            admin.execute(sql.SQL("set search_path to {}, public").format(sql.Identifier(namespace)))
            admin.execute(SCHEMA.read_text(encoding="utf-8"), prepare=False)
            org = admin.execute("""insert into research_organization(slug,name)
                                   values('standing-service','standing service') returning id""").fetchone()[0]
            project = admin.execute("""insert into research_project(organization_id,slug,name,status)
                                       values(%s,'standing-service','standing service','active') returning id""",
                                    (org,)).fetchone()[0]
            admin.execute("""insert into research_project_member(project_id,actor_id,role)
                             values(%s,'owner@example.com','owner')""", (project,))
            video = admin.execute("""insert into source_video(platform,platform_video_id,title,source_url)
                                     values('douyin','1234567890','public example',
                                       'https://www.iesdouyin.com/share/video/1234567890/?region=US') returning id""").fetchone()[0]
            admin.execute("""insert into project_video_inclusion(project_id,video_id,source_type,status)
                             values(%s,%s,'manual','accepted')""", (project, video))
            parent = admin.execute("""insert into media_asset(video_id,kind,storage_location,bucket,object_key,
                                       content_sha256,size_bytes,content_type)
                                      values(%s,'video','s3','test',%s,%s,1024,'video/mp4') returning id""",
                                   (video, "sha256/bb/" + MANIFEST, MANIFEST)).fetchone()[0]
            asset = admin.execute("""insert into media_asset(video_id,kind,storage_location,bucket,object_key,
                                      content_sha256,size_bytes,content_type,parent_asset_id)
                                     values(%s,'audio','s3','test',%s,%s,64,'audio/wav',%s) returning id""",
                                  (video, f"sha256/{FINGERPRINT[:2]}/{FINGERPRINT}", FINGERPRINT, parent)).fetchone()[0]
            run = admin.execute("""insert into pipeline_run(run_type,run_version,platform,status)
                                    values('media_ingestion','v1','douyin','success') returning id""").fetchone()[0]
            admin.execute("""insert into pipeline_run_item(run_id,entity_type,entity_id,stage,outcome,metadata)
                             values(%s,'video',%s,'media','success',%s)""",
                          (run, video, psycopg.types.json.Jsonb({"asset_ids": [str(parent), str(asset)]})))
            scoped_dsn = make_conninfo(DSN, options=f"-c search_path={namespace},public")
            monkeypatch.setenv("WM_END_USER_EMAIL", "owner@example.com")
            service = ProjectASRService(scoped_dsn, delivery_origin="https://media.example.test",
                                        trusted_worker_actor="owner@example.com",
                                        trusted_storage_location="s3", trusted_bucket="test")
            admin.execute("update research_project_member set role='admin' where project_id=%s", (project,))
            with pytest.raises(PermissionError, match="project owner is required"):
                service.authorize_standing(project_id=project)
            admin.execute("update research_project_member set role='owner' where project_id=%s", (project,))
            grant = service.authorize_standing(project_id=project)["grant"]
            assert grant and grant["status"] == "active" and grant["provider"] == PROVIDER
            orphan = admin.execute("""insert into media_asset(video_id,kind,storage_location,bucket,object_key,
                                      content_sha256,size_bytes,content_type)
                                     values(%s,'audio','s3','test',%s,%s,64,'audio/wav') returning id""",
                                   (video, "sha256/cc/" + "c" * 64, "c" * 64)).fetchone()[0]
            with pytest.raises(PermissionError, match="parent-linked"):
                service.authorize_standing_asset(project_id=project, video_id=video, asset_id=orphan)
            admin.execute("update pipeline_run set status='failed' where id=%s", (run,))
            with pytest.raises(PermissionError, match="ingestion provenance"):
                service.authorize_standing_asset(project_id=project, video_id=video, asset_id=asset)
            admin.execute("update pipeline_run set status='success' where id=%s", (run,))
            result = service.authorize_standing_asset(project_id=project, video_id=video, asset_id=asset)
            assert result["status"] == "approved" and result["listening_status"] == "not_listened"
            assert service.authorize_standing_asset(project_id=project, video_id=video, asset_id=asset)["review_id"] == result["review_id"]
            row = admin.execute("""select authorization_kind,reviewed_by,reviewed_at,
                                  review_statement->>'listening_status' from project_asr_media_review where id=%s""",
                                (result["review_id"],)).fetchone()
            assert row == ("standing_grant", None, None, "not_listened")
            manifest = admin.execute("""select asset_manifest_fingerprint from project_asr_media_review
                                        where id=%s""", (result["review_id"],)).fetchone()[0]
            dispatch = ProjectASRDispatch(project, video, result["review_id"], PROVIDER,
                                          "test-model", "v1", "wav-v1", FINGERPRINT)
            with psycopg.connect(scoped_dsn, row_factory=dict_row) as check:
                service._assert_current_review(check, project, video, result["review_id"], dispatch,
                                               manifest, expected_review_version=f"standing-media-v1:{grant['id']}")
            service.revoke_standing(project_id=project, grant_id=grant["id"])
            with pytest.raises(PermissionError, match="active project standing ASR grant"):
                service.authorize_standing_asset(project_id=project, video_id=video, asset_id=asset)
            with psycopg.connect(scoped_dsn, row_factory=dict_row) as check:
                with pytest.raises(PermissionError, match="revoked before provider HTTP"):
                    service._assert_current_review(check, project, video, result["review_id"], dispatch,
                                                   manifest, expected_review_version=f"standing-media-v1:{grant['id']}")
            replacement = service.authorize_standing(project_id=project)["grant"]
            assert replacement and replacement["id"] != grant["id"]
            rebound = service.authorize_standing_asset(project_id=project, video_id=video, asset_id=asset)
            assert rebound["status"] == "approved" and rebound["review_id"] != result["review_id"]
        finally:
            admin.execute("set search_path to public")
            admin.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
