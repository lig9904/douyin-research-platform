from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/041_project_las_live_review.sql"
MIGRATION_042 = ROOT / "db/migrations/042_project_las_version_binding.sql"
MIGRATION_043 = ROOT / "db/migrations/043_project_las_machine_authorization.sql"
SCHEMA = ROOT / "db/schema.sql"
DSN = os.getenv("TEST_DATABASE_URL")
MARKER = "-- Project-local whole-video review"
MARKER_042 = "-- 041 approvals bound a database media reference"
MARKER_043 = "-- Cloud processing permission and human whole-video review are different facts."


def test_041_schema_is_consolidated_and_live_review_is_required() -> None:
    migration = MIGRATION.read_text(encoding="utf-8")
    schema = SCHEMA.read_text(encoding="utf-8")
    assert migration.startswith(MARKER)
    assert schema.split(MARKER, 1)[1].split(MARKER_042, 1)[0] == migration.split(MARKER, 1)[1]
    assert "project_las_live_review_required" in migration
    assert "project_las_live_provider_required" in migration
    assert "for update of review" in migration
    assert "project_las_analysis_result" in migration
    assert "LAS machine summary must match completed private receipt" in migration


def test_042_schema_is_consolidated_and_requires_concrete_versions() -> None:
    migration = MIGRATION_042.read_text(encoding="utf-8")
    schema = SCHEMA.read_text(encoding="utf-8")
    assert migration.startswith(MARKER_042)
    assert (schema.split(MARKER_042, 1)[1].split(MARKER_043, 1)[0].rstrip("\n") ==
            migration.split(MARKER_042, 1)[1].rstrip("\n"))
    assert not any(
        line.strip().lower() in {"begin;", "commit;"}
        for line in migration.splitlines()
    ), "the release runner must own the migration and ledger transaction"
    assert "legacy_object_version_unbound" in migration
    assert "new live LAS attempt requires matching reviewed object version" in migration
    assert "LAS receipt byte version must match its attempt" in migration


def test_043_schema_separates_machine_authorization_from_human_review() -> None:
    migration = MIGRATION_043.read_text(encoding="utf-8")
    schema = SCHEMA.read_text(encoding="utf-8")
    assert migration.startswith(MARKER_043)
    assert schema.split(MARKER_043, 1)[1] == migration.split(MARKER_043, 1)[1]
    assert "project_las_video_authorization" in migration
    assert "project_las_one_permission_source" in migration
    assert "human_review_status' is distinct from 'not_asserted'" in migration
    assert "authorized_machine_first_and_result" in migration


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_043_upgrades_existing_review_without_rewriting_it_and_replays_atomically() -> None:
    assert DSN
    namespace = f"las_machine_upgrade_{uuid4().hex}"
    previous_schema = SCHEMA.read_text(encoding="utf-8").split(MARKER_043, 1)[0]
    migration = MIGRATION_043.read_text(encoding="utf-8")
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            with conn.transaction():
                conn.execute(previous_schema, prepare=False)
            org = conn.execute(
                "insert into research_organization(slug,name) values('las-043','LAS') returning id"
            ).fetchone()[0]
            project = conn.execute(
                """insert into research_project(organization_id,slug,name,status)
                   values(%s,'las-043','LAS','active') returning id""", (org,),
            ).fetchone()[0]
            conn.execute(
                """insert into research_project_member(project_id,actor_id,role,status)
                   values(%s,'owner@example.com','owner','active')""", (project,),
            )
            video = conn.execute(
                """insert into source_video(platform,platform_video_id,title)
                   values('douyin',%s,'Existing LAS review') returning id""",
                (uuid4().hex,),
            ).fetchone()[0]
            conn.execute(
                """insert into project_video_inclusion(project_id,video_id,source_type,status)
                   values(%s,%s,'manual','accepted')""", (project, video),
            )
            asset = conn.execute(
                """insert into media_asset(video_id,kind,storage_location,bucket,object_key,
                     content_sha256,size_bytes,content_type)
                   values(%s,'video','s3','private',%s,%s,1024,'video/mp4') returning id""",
                (video, "sha256/aa/" + "a" * 64, "a" * 64),
            ).fetchone()[0]
            review = conn.execute(
                """insert into project_las_video_review(
                     project_id,video_id,asset_id,asset_sha256,asset_manifest_fingerprint,
                     delivery_origin,review_version,review_statement,object_version_id)
                   values(%s,%s,%s,%s,%s,'https://media.example','las-video-v2',
                          '{"consent_statement":"I approved the whole video"}'::jsonb,
                          'existing-version-A') returning id""",
                (project, video, asset, "a" * 64, "b" * 64),
            ).fetchone()[0]
            conn.execute(
                """update project_las_video_review set status='approved',
                   reviewed_by='owner@example.com' where id=%s""", (review,),
            )
            attempt = conn.execute(
                """insert into project_las_analysis_attempt(
                     project_id,video_id,asset_id,asset_sha256,task_key,attempt_no,
                     provider,account_scope,cost_currency,status,authorization_ref,
                     authorized_by,record_mode,input_fingerprint,video_review_id,
                     requested_by,object_version_id)
                   values(%s,%s,%s,%s,'existing-043',1,'volcengine_las','test-account',
                          'CNY','prepared',%s,'owner@example.com','live_pre_dispatch',
                          %s,%s,'owner@example.com','existing-version-A') returning id""",
                (project, video, asset, "a" * 64, str(review), "c" * 64, review),
            ).fetchone()[0]
            with pytest.raises(RuntimeError, match="simulated ledger failure"):
                with conn.transaction():
                    conn.execute(migration, prepare=False)
                    raise RuntimeError("simulated ledger failure")
            assert conn.execute(
                "select to_regclass(%s)", (f"{namespace}.project_las_video_authorization",),
            ).fetchone()[0] is None
            assert conn.execute(
                "select status,video_review_id from project_las_analysis_attempt where id=%s",
                (attempt,),
            ).fetchone() == ("prepared", review)
            with conn.transaction():
                conn.execute(migration, prepare=False)
            with conn.transaction():
                conn.execute(migration, prepare=False)
            assert conn.execute(
                """select status,video_review_id,video_authorization_id,object_version_id
                   from project_las_analysis_attempt where id=%s""", (attempt,),
            ).fetchone() == ("prepared", review, None, "existing-version-A")
            assert conn.execute(
                "select status,object_version_id from project_las_video_review where id=%s",
                (review,),
            ).fetchone() == ("approved", "existing-version-A")
        finally:
            conn.execute("set search_path to public")
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_041_review_binding_replay_revocation_and_single_claim() -> None:
    assert DSN
    namespace = f"las_live_{uuid4().hex}"
    previous_schema = SCHEMA.read_text(encoding="utf-8").split(MARKER, 1)[0]
    migration = MIGRATION.read_text(encoding="utf-8")
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            conn.execute(previous_schema, prepare=False)
            conn.execute(migration, prepare=False)
            conn.execute(migration, prepare=False)
            org = conn.execute("insert into research_organization(slug,name) values('las-org','LAS') returning id").fetchone()[0]
            project = conn.execute(
                "insert into research_project(organization_id,slug,name,status) values(%s,'las-project','LAS','active') returning id",
                (org,),
            ).fetchone()[0]
            conn.execute(
                """insert into research_project_member(project_id,actor_id,role,status)
                   values(%s,'owner@example.com','owner','active')""", (project,),
            )
            conn.execute(
                """insert into research_project_member(project_id,actor_id,role,status)
                   values(%s,'viewer@example.com','viewer','active')""", (project,),
            )
            video = conn.execute(
                "insert into source_video(platform,platform_video_id,title) values('douyin',%s,'LAS case') returning id",
                (f"test-{uuid4().hex}",),
            ).fetchone()[0]
            conn.execute(
                "insert into project_video_inclusion(project_id,video_id,source_type,status) values(%s,%s,'manual','accepted')",
                (project, video),
            )
            asset_sha = "a" * 64
            asset = conn.execute(
                """insert into media_asset(video_id,kind,storage_location,bucket,object_key,
                     content_sha256,size_bytes,content_type)
                   values(%s,'video','s3','private',%s,%s,1024,'video/mp4') returning id""",
                (video, "sha256/aa/" + asset_sha, asset_sha),
            ).fetchone()[0]

            def review(version: str):
                row = conn.execute(
                    """insert into project_las_video_review(project_id,video_id,asset_id,
                         asset_sha256,asset_manifest_fingerprint,delivery_origin,review_version,
                         review_statement)
                       values(%s,%s,%s,%s,%s,'https://media.example',%s,
                              '{"consent_statement":"I approve whole-video cloud analysis"}'::jsonb)
                       returning id""",
                    (project, video, asset, asset_sha, "b" * 64, version),
                ).fetchone()[0]
                conn.execute(
                    "update project_las_video_review set status='approved',reviewed_by='owner@example.com' where id=%s",
                    (row,),
                )
                return row

            review_one = review("v1")

            def attempt(key: str, review_id, *, provider="volcengine_las",
                        requested_by="owner@example.com"):
                return conn.execute(
                    """insert into project_las_analysis_attempt(project_id,video_id,asset_id,
                         asset_sha256,task_key,attempt_no,provider,account_scope,cost_currency,
                         status,authorization_ref,authorized_by,record_mode,input_fingerprint,
                         video_review_id,requested_by)
                       values(%s,%s,%s,%s,%s,1,%s,'test-account','CNY','prepared',%s,
                              'owner@example.com','live_pre_dispatch',%s,%s,%s) returning id""",
                    (project, video, asset, asset_sha, key, provider, str(review_id), "c" * 64,
                     review_id, requested_by),
                ).fetchone()[0]

            with pytest.raises(psycopg.Error):
                with conn.transaction():
                    attempt("missing-review", None)
            with pytest.raises(psycopg.Error, match="project_las_live_provider_required"):
                with conn.transaction():
                    attempt("wrong-provider", review_one, provider="other")
            with pytest.raises(psycopg.Error, match="authenticated project manager"):
                with conn.transaction():
                    attempt("viewer-request", review_one, requested_by="viewer@example.com")
            first = attempt("first", review_one)
            conn.execute(
                "update project_las_video_review set status='revoked',revoked_by='owner@example.com' where id=%s",
                (review_one,),
            )
            with pytest.raises(psycopg.Error, match="current project video review"):
                with conn.transaction():
                    conn.execute(
                        "update project_las_analysis_attempt set status='submitting',submission_count=1 where id=%s",
                        (first,),
                    )
            review_two = review("v2")
            second = attempt("second", review_two)
            conn.execute(
                "update project_las_analysis_attempt set status='submitting',submission_count=1 where id=%s",
                (second,),
            )
            assert conn.execute(
                "select status,submission_count from project_las_analysis_attempt where id=%s", (second,),
            ).fetchone() == ("submitting", 1)
            with pytest.raises(psycopg.Error, match="submission may be claimed once"):
                with conn.transaction():
                    conn.execute(
                        "update project_las_analysis_attempt set status='submitting',submission_count=0 where id=%s",
                        (second,),
                    )
        finally:
            conn.execute("set search_path to public")
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_041_upgrade_preserves_preexisting_unreviewed_live_attempt_without_enabling_it() -> None:
    assert DSN
    namespace = f"las_upgrade_{uuid4().hex}"
    previous_schema = SCHEMA.read_text(encoding="utf-8").split(MARKER, 1)[0]
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            conn.execute(previous_schema, prepare=False)
            org = conn.execute(
                "insert into research_organization(slug,name) values('las-old','LAS') returning id"
            ).fetchone()[0]
            project = conn.execute(
                """insert into research_project(organization_id,slug,name,status)
                   values(%s,'las-old','LAS','active') returning id""", (org,),
            ).fetchone()[0]
            video = conn.execute(
                """insert into source_video(platform,platform_video_id,title)
                   values('douyin',%s,'LAS old task') returning id""", (uuid4().hex,),
            ).fetchone()[0]
            conn.execute(
                """insert into project_video_inclusion(project_id,video_id,source_type,status)
                   values(%s,%s,'manual','accepted')""", (project, video),
            )
            asset = conn.execute(
                """insert into media_asset(video_id,kind,storage_location,bucket,object_key,
                     content_sha256,size_bytes,content_type)
                   values(%s,'video','s3','private',%s,%s,1024,'video/mp4') returning id""",
                (video, 'sha256/aa/' + 'a' * 64, 'a' * 64),
            ).fetchone()[0]
            old_id = conn.execute(
                """insert into project_las_analysis_attempt(
                     project_id,video_id,asset_id,asset_sha256,task_key,attempt_no,
                     provider,account_scope,cost_currency,status,authorization_ref,
                     authorized_by,record_mode,input_fingerprint)
                   values(%s,%s,%s,%s,%s,1,'legacy_las','old-account','CNY',
                          'prepared','old-review','owner@example.com','live_pre_dispatch',%s)
                   returning id""",
                (project, video, asset, 'a' * 64, uuid4().hex, 'b' * 64),
            ).fetchone()[0]
            claimed_id = conn.execute(
                """insert into project_las_analysis_attempt(
                     project_id,video_id,asset_id,asset_sha256,task_key,attempt_no,
                     provider,account_scope,cost_currency,status,authorization_ref,
                     authorized_by,record_mode,input_fingerprint)
                   values(%s,%s,%s,%s,%s,1,'legacy_las','old-account','CNY',
                          'prepared','old-review','owner@example.com','live_pre_dispatch',%s)
                   returning id""",
                (project, video, asset, 'a' * 64, uuid4().hex, 'c' * 64),
            ).fetchone()[0]
            conn.execute(
                "update project_las_analysis_attempt set status='submitting',submission_count=1 where id=%s",
                (claimed_id,),
            )
            conn.execute(MIGRATION.read_text(encoding="utf-8"), prepare=False)
            assert conn.execute(
                "select status,error_code from project_las_analysis_attempt where id=%s", (old_id,),
            ).fetchone() == (
                'cancelled', 'legacy_live_unreviewed_requires_manual_reconciliation'
            )
            assert conn.execute(
                "select status,error_code from project_las_analysis_attempt where id=%s",
                (claimed_id,),
            ).fetchone() == (
                'unknown', 'legacy_live_unreviewed_requires_manual_reconciliation'
            )
            with pytest.raises(psycopg.Error):
                with conn.transaction():
                    conn.execute(
                        """update project_las_analysis_attempt
                              set status='submitting',submission_count=1 where id=%s""",
                        (old_id,),
                    )
            conn.execute(
                "update project_las_analysis_attempt set error_code='manually_reconciled' where id=%s",
                (old_id,),
            )
            assert conn.execute(
                """select convalidated from pg_constraint where conname='project_las_live_review_required'
                   and connamespace=%s::regnamespace""", (namespace,),
            ).fetchone()[0] is False
        finally:
            conn.execute("set search_path to public")
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_042_upgrade_quarantines_prepared_and_refuses_unresolved_legacy_claim() -> None:
    assert DSN
    namespace = f"las_version_{uuid4().hex}"
    previous_schema = SCHEMA.read_text(encoding="utf-8").split(MARKER_042, 1)[0]
    migration = MIGRATION_042.read_text(encoding="utf-8")
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            conn.execute(previous_schema, prepare=False)
            org = conn.execute(
                "insert into research_organization(slug,name) values('las-version','LAS') returning id"
            ).fetchone()[0]
            project = conn.execute(
                """insert into research_project(organization_id,slug,name,status)
                   values(%s,'las-version','LAS','active') returning id""", (org,),
            ).fetchone()[0]
            conn.execute(
                """insert into research_project_member(project_id,actor_id,role,status)
                   values(%s,'owner@example.com','owner','active')""", (project,),
            )
            video = conn.execute(
                """insert into source_video(platform,platform_video_id,title)
                   values('douyin',%s,'LAS legacy case') returning id""", (uuid4().hex,),
            ).fetchone()[0]
            conn.execute(
                """insert into project_video_inclusion(project_id,video_id,source_type,status)
                   values(%s,%s,'manual','accepted')""", (project, video),
            )
            asset_sha = "a" * 64
            asset = conn.execute(
                """insert into media_asset(video_id,kind,storage_location,bucket,object_key,
                     content_sha256,size_bytes,content_type)
                   values(%s,'video','s3','private',%s,%s,1024,'video/mp4') returning id""",
                (video, "sha256/aa/" + asset_sha, asset_sha),
            ).fetchone()[0]
            old_review = conn.execute(
                """insert into project_las_video_review(project_id,video_id,asset_id,
                     asset_sha256,asset_manifest_fingerprint,delivery_origin,review_version,
                     review_statement)
                   values(%s,%s,%s,%s,%s,'https://media.example','las-video-v1',
                          '{"consent_statement":"I approve whole-video cloud analysis"}'::jsonb)
                   returning id""",
                (project, video, asset, asset_sha, "b" * 64),
            ).fetchone()[0]
            conn.execute(
                """update project_las_video_review set status='approved',
                   reviewed_by='owner@example.com' where id=%s""", (old_review,),
            )

            def old_attempt(key: str):
                return conn.execute(
                    """insert into project_las_analysis_attempt(project_id,video_id,asset_id,
                         asset_sha256,task_key,attempt_no,provider,account_scope,cost_currency,
                         status,authorization_ref,authorized_by,record_mode,input_fingerprint,
                         video_review_id,requested_by)
                       values(%s,%s,%s,%s,%s,1,'volcengine_las','test-account','CNY',
                              'prepared',%s,'owner@example.com','live_pre_dispatch',%s,%s,
                              'owner@example.com') returning id""",
                    (project, video, asset, asset_sha, key, str(old_review), "c" * 64,
                     old_review),
                ).fetchone()[0]

            prepared = old_attempt("legacy-prepared")
            claimed = old_attempt("legacy-claimed")
            conn.execute(
                """update project_las_analysis_attempt
                   set status='submitting',submission_count=1 where id=%s""", (claimed,),
            )
            with pytest.raises(psycopg.Error, match="unresolved legacy LAS Submit claim"):
                conn.execute(migration, prepare=False)
            conn.execute("rollback")
            assert conn.execute(
                "select count(*) from information_schema.columns where table_schema=%s "
                "and table_name='project_las_analysis_attempt' and column_name='object_version_id'",
                (namespace,),
            ).fetchone()[0] == 0
            # Model the old Worker settling its original task; never invent a
            # byte VersionId for that task. Only then is the cutover safe.
            conn.execute(
                "update project_las_analysis_attempt set status='unknown', "
                "error_code='submit_outcome_unknown' where id=%s", (claimed,),
            )
            with pytest.raises(RuntimeError, match="simulated ledger failure"):
                with conn.transaction():
                    conn.execute(migration, prepare=False)
                    raise RuntimeError("simulated ledger failure")
            assert conn.execute(
                "select count(*) from information_schema.columns where table_schema=%s "
                "and table_name='project_las_analysis_attempt' and column_name='object_version_id'",
                (namespace,),
            ).fetchone()[0] == 0
            assert conn.execute(
                "select status from project_las_analysis_attempt where id=%s", (prepared,),
            ).fetchone()[0] == "prepared"
            conn.execute(migration, prepare=False)
            conn.execute(migration, prepare=False)
            assert conn.execute(
                """select status,error_code,object_version_id
                   from project_las_analysis_attempt where id=%s""", (prepared,),
            ).fetchone() == ("cancelled", "legacy_object_version_unbound", None)
            assert conn.execute(
                "select status,object_version_id from project_las_analysis_attempt where id=%s",
                (claimed,),
            ).fetchone() == ("unknown", None)
            with pytest.raises(psycopg.Error, match="matching reviewed object version"):
                with conn.transaction():
                    old_attempt("legacy-replay")
            with pytest.raises(psycopg.Error, match="byte version is immutable"):
                with conn.transaction():
                    conn.execute(
                        """update project_las_video_review
                           set object_version_id='invented' where id=%s""", (old_review,),
                    )
        finally:
            conn.execute("set search_path to public")
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
