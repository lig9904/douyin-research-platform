from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "db/schema.sql"
MIGRATION = ROOT / "db/migrations/032_project_private_asr_l3.sql"
DSN = os.getenv("TEST_DATABASE_URL")


def test_project_private_asr_l3_contract_is_scoped_and_review_gated() -> None:
    migration = MIGRATION.read_text(encoding="utf-8")
    schema = SCHEMA.read_text(encoding="utf-8")
    for source in (migration, schema):
        for table in (
            "project_asr_media_review",
            "project_research_task_cost",
            "project_asr_execution_job",
            "project_transcript",
            "project_l3_privacy_review",
            "project_l3_execution_job",
            "project_l3_analysis_result",
        ):
            assert table in source
        assert "project_video_inclusion(project_id, video_id)" in source
        assert "media_asset(id, video_id)" in source
        assert "asset.kind='audio' and asset.content_type='audio/wav'" in source
        assert "windmill_end_user_email_allowlist_v1" in source
        assert "must be revoked, not deleted" in source
        assert "is immutable" in source
        assert "current approved project media review" in source
        assert "current approved project privacy review" in source
        assert "for update" in source
        assert "immediately before Provider HTTP" in source
    # Project-private result storage cannot fall back to the global L2/L3 tables.
    assert "references transcript(" not in migration
    assert "references analysis_run(" not in migration
    assert "references research_task_cost(" not in migration


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_032_rejects_cross_project_or_revoked_asr_approval_before_submission() -> None:
    assert DSN
    namespace = f"project_private_asr_l3_{uuid4().hex}"
    marker = "-- Project-private ASR/L3 is deliberately separate"
    baseline_031 = SCHEMA.read_text(encoding="utf-8").split(marker, 1)[0]
    digest = "a" * 64
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}, public").format(sql.Identifier(namespace)))
            conn.execute(baseline_031, prepare=False)
            conn.execute(MIGRATION.read_text(encoding="utf-8"), prepare=False)
            org = conn.execute(
                "insert into research_organization(slug,name) values('private-org','私有项目组织') returning id"
            ).fetchone()[0]
            project_a = conn.execute(
                """insert into research_project(organization_id,slug,name,status)
                   values(%s,'private-a','项目 A','active') returning id""",
                (org,),
            ).fetchone()[0]
            project_b = conn.execute(
                """insert into research_project(organization_id,slug,name,status)
                   values(%s,'private-b','项目 B','active') returning id""",
                (org,),
            ).fetchone()[0]
            video = conn.execute(
                """insert into source_video(platform,platform_video_id,title)
                   values('douyin','3200000000000000001','项目私有音频') returning id"""
            ).fetchone()[0]
            for project in (project_a, project_b):
                conn.execute(
                    """insert into project_video_inclusion(project_id,video_id,source_type,source_ref,status)
                       values(%s,%s,'manual','test','accepted')""",
                    (project, video),
                )
            asset = conn.execute(
                """insert into media_asset(video_id,kind,storage_location,bucket,object_key,content_sha256,size_bytes,content_type)
                   values(%s,'audio','s3','test',%s,%s,64,'audio/wav') returning id""",
                (video, f"sha256/{digest[:2]}/{digest}", digest),
            ).fetchone()[0]
            review = conn.execute(
                """insert into project_asr_media_review(
                     project_id,video_id,asset_id,review_version,media_fingerprint,delivery_origin,identity_source,review_statement
                   ) values(%s,%s,%s,'v1',%s,'project-preview','windmill_end_user_email_allowlist_v1','{}') returning id""",
                (project_a, video, asset, digest),
            ).fetchone()[0]
            conn.execute(
                "update project_asr_media_review set status='approved',reviewed_by='owner@example.com' where id=%s",
                (review,),
            )
            job_values = (
                "private-asr-" + uuid4().hex,
                project_a,
                video,
                review,
                asset,
                digest,
                digest,
            )
            conn.execute(
                """insert into project_asr_execution_job(
                     task_key,project_id,video_id,media_review_id,reviewed_asset_id,review_version,media_fingerprint,
                     provider,model_id,model_revision,engine_version,source_fingerprint,status,cost_currency
                   ) values(%s,%s,%s,%s,%s,'v1',%s,'test','test','1','wav-v1',%s,'queued','CNY')""",
                job_values,
            )
            conn.execute(
                "update project_asr_execution_job set status='submitting' where task_key=%s", (job_values[0],)
            )
            conn.execute(
                "update project_asr_media_review set status='revoked',revoked_by='owner@example.com' where id=%s", (review,)
            )
            pre_revocation_job = "private-asr-revoked-" + uuid4().hex
            conn.execute(
                """insert into project_asr_execution_job(
                     task_key,project_id,video_id,media_review_id,reviewed_asset_id,review_version,media_fingerprint,
                     provider,model_id,model_revision,engine_version,source_fingerprint,status,cost_currency
                   ) values(%s,%s,%s,%s,%s,'v1',%s,'test','test','1','wav-v1',%s,'queued','CNY')""",
                (pre_revocation_job, project_a, video, review, asset, digest, digest),
            )
            # A worker must make this transition before issuing Provider HTTP;
            # a pre-dispatch revocation makes it fail at the database boundary.
            with pytest.raises(psycopg.Error, match="current approved project media review"):
                conn.execute(
                    "update project_asr_execution_job set status='submitting' where task_key=%s", (pre_revocation_job,)
                )
            with pytest.raises(psycopg.Error, match="current approved project media review"):
                conn.execute(
                    "update project_asr_execution_job set status='running' where task_key=%s", (job_values[0],)
                )
            with pytest.raises(psycopg.Error):
                conn.execute(
                    """insert into project_asr_execution_job(
                         task_key,project_id,video_id,media_review_id,reviewed_asset_id,review_version,media_fingerprint,
                         provider,model_id,model_revision,engine_version,source_fingerprint,status,cost_currency
                       ) values(%s,%s,%s,%s,%s,'v1',%s,'test','test','1','wav-v1',%s,'queued','CNY')""",
                    ("cross-project-" + uuid4().hex, project_b, video, review, asset, digest, digest),
                )
        finally:
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
