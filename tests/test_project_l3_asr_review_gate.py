"""PostgreSQL boundary tests for project L3 after an ASR review revocation."""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from douyin_research.project_analysis.l3 import (
    ProjectL3EvidenceService,
    ProjectL3ExecutionSelection,
    ProjectL3ExecutionService,
)
from douyin_research.project_analysis.asr_backend import ProjectASRService
from douyin_research.l2.asr_execution import ASRProviderState
from douyin_research.l2.transcripts import TaskCost, TranscriptEvidence


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "db/schema.sql"
MIGRATION = ROOT / "db/migrations/032_project_private_asr_l3.sql"
DSN = os.getenv("TEST_DATABASE_URL")


def _scoped_dsn(namespace: str) -> str:
    assert DSN
    return make_conninfo(DSN, options=f"-c search_path={namespace},public")


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_project_l3_rejects_revoked_or_cross_project_asr_transcript_before_provider() -> None:
    """A historic transcript never outlives the project ASR approval that made it legal."""
    assert DSN
    namespace = f"project_l3_asr_gate_{uuid4().hex}"
    marker = "-- Project-private ASR/L3 is deliberately separate"
    baseline_031 = SCHEMA.read_text(encoding="utf-8").split(marker, 1)[0]
    digest, manifest_digest, text_digest = "a" * 64, "b" * 64, "c" * 64
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}, public").format(sql.Identifier(namespace)))
            conn.execute(baseline_031, prepare=False)
            conn.execute(MIGRATION.read_text(encoding="utf-8"), prepare=False)
            org = conn.execute("insert into research_organization(slug,name) values('l3-gate-org','L3 门禁组织') returning id").fetchone()[0]
            project_a = conn.execute("insert into research_project(organization_id,slug,name,status) values(%s,'l3-gate-a','项目 A','active') returning id", (org,)).fetchone()[0]
            project_b = conn.execute("insert into research_project(organization_id,slug,name,status) values(%s,'l3-gate-b','项目 B','active') returning id", (org,)).fetchone()[0]
            video = conn.execute("insert into source_video(platform,platform_video_id,title) values('douyin','3200000000000000099','项目私有 L3 审核门禁') returning id").fetchone()[0]
            for project in (project_a, project_b):
                conn.execute("insert into project_video_inclusion(project_id,video_id,source_type,source_ref,status) values(%s,%s,'manual','test','accepted')", (project, video))
            conn.execute("""insert into video_comment_feature_snapshot(
                video_id,feature_version,evidence_fingerprint,sampled_comment_count,root_comment_count,
                sampled_reply_count,source_observation_count,text_present_count,question_text_count,
                like_known_count,reply_known_count) values(%s,'test-v1',%s,0,0,0,0,0,0,0,0)""", (video, digest))
            asset = conn.execute("""insert into media_asset(video_id,kind,storage_location,bucket,object_key,content_sha256,size_bytes,content_type)
                values(%s,'audio','s3','test',%s,%s,64,'audio/wav') returning id""", (video, f"sha256/{digest[:2]}/{digest}", digest)).fetchone()[0]
            asr_review = conn.execute("""insert into project_asr_media_review(
                project_id,video_id,asset_id,review_version,media_fingerprint,asset_manifest_fingerprint,
                delivery_origin,identity_source,review_statement) values(%s,%s,%s,'asr-v1',%s,%s,
                'project-preview','windmill_end_user_email_allowlist_v1','{}') returning id""", (project_a, video, asset, digest, manifest_digest)).fetchone()[0]
            conn.execute("update project_asr_media_review set status='approved',reviewed_by='owner@example.com' where id=%s", (asr_review,))
            asr_job = conn.execute("""insert into project_asr_execution_job(
                task_key,project_id,video_id,media_review_id,reviewed_asset_id,review_version,media_fingerprint,
                asset_manifest_fingerprint,provider,model_id,model_revision,engine_version,source_fingerprint,status,cost_currency)
                values(%s,%s,%s,%s,%s,'asr-v1',%s,%s,'test','test','1','wav-v1',%s,'completed','CNY') returning id""",
                ("asr-gate-" + uuid4().hex, project_a, video, asr_review, asset, digest, manifest_digest, digest)).fetchone()[0]
            transcript = conn.execute("""insert into project_transcript(
                project_id,video_id,execution_job_id,media_review_id,asr_provider,text_content,text_fingerprint)
                values(%s,%s,%s,%s,'test','有完整人声的项目私有转写',%s) returning id""",
                (project_a, video, asr_job, asr_review, text_digest)).fetchone()[0]

            # Provider no-speech and failed responses are paid/unknown task
            # facts, not empty transcripts or invisible zero-cost runs.
            service = ProjectASRService(_scoped_dsn(namespace), delivery_origin="https://media.example.test")
            def pending_job(status: str):
                return conn.execute("""insert into project_asr_execution_job(
                    task_key,project_id,video_id,media_review_id,reviewed_asset_id,
                    review_version,media_fingerprint,asset_manifest_fingerprint,
                    provider,model_id,model_revision,engine_version,source_fingerprint,
                    status,provider_task_ref,cost_currency)
                    values(%s,%s,%s,%s,%s,'asr-v1',%s,%s,'test','test','1','wav-v1',%s,
                    %s,%s,'CNY') returning id""",
                    ("asr-outcome-" + uuid4().hex, project_a, video, asr_review, asset,
                     digest, manifest_digest, digest, status, "provider-" + uuid4().hex)).fetchone()[0]

            no_speech_job = pending_job("running")
            no_speech = TranscriptEvidence(
                asr_provider="test", model_id="test", model_revision="1", engine_version="wav-v1",
                source_fingerprint=digest, text="", quality_status="no_speech",
            )
            unknown = TaskCost(api_cost=None, asr_cost=None, llm_cost=Decimal("0"),
                               currency="CNY", basis="unknown")
            service._record_state(project_a, video, asr_review, no_speech_job,
                                  ASRProviderState("completed", "provider-no-speech", no_speech, unknown),
                                  "CNY", counted_poll=True)
            no_speech_row = conn.execute("""select job.status,job.error_code,job.poll_count,
                    cost.status,cost.cost_basis,cost.total_cost
                    from project_asr_execution_job job join project_research_task_cost cost
                    on cost.id=job.task_cost_id where job.id=%s""", (no_speech_job,)).fetchone()
            assert no_speech_row == ("failed", "project_asr_no_speech", 1, "failed", "unknown", None)
            assert conn.execute("select count(*) from project_transcript where execution_job_id=%s",
                                (no_speech_job,)).fetchone()[0] == 0

            failed_job = pending_job("running")
            service._record_state(project_a, video, asr_review, failed_job,
                                  ASRProviderState("failed", "provider-failed", cost=unknown,
                                                   error_code="provider_failure"), "CNY")
            assert conn.execute("""select cost.status,cost.cost_basis,cost.total_cost
                from project_asr_execution_job job join project_research_task_cost cost
                on cost.id=job.task_cost_id where job.id=%s""", (failed_job,)).fetchone() == ("failed", "unknown", None)

            ambiguous_job = pending_job("submitting")
            service._mark_reconciliation_required(ambiguous_job)
            assert conn.execute("""select job.status,job.error_code,cost.status,cost.cost_basis,cost.total_cost
                from project_asr_execution_job job join project_research_task_cost cost
                on cost.id=job.task_cost_id where job.id=%s""", (ambiguous_job,)).fetchone() == (
                    "submitting", "project_asr_reconciliation_required", "failed", "unknown", None)

            scoped_dsn = _scoped_dsn(namespace)
            evidence = ProjectL3EvidenceService(scoped_dsn).prepare(
                project_id=project_a, video_id=video, transcript_id=transcript, review_version="privacy-v1"
            )
            l3_review = conn.execute("""insert into project_l3_privacy_review(
                project_id,video_id,transcript_id,review_version,evidence_fingerprint,evidence_manifest)
                values(%s,%s,%s,'privacy-v1',%s,'{}') returning id""",
                (project_a, video, transcript, evidence.fingerprint)).fetchone()[0]
            conn.execute("update project_l3_privacy_review set status='approved',reviewed_by='owner@example.com' where id=%s", (l3_review,))

            # Same canonical video is included in B, but A's private transcript
            # and its ASR approval must remain unreadable from B.
            with pytest.raises(PermissionError, match="PROJECT_L3_EVIDENCE_UNAVAILABLE"):
                ProjectL3EvidenceService(scoped_dsn).prepare(
                    project_id=project_b, video_id=video, transcript_id=transcript, review_version="privacy-v1"
                )

            conn.execute("update project_asr_media_review set status='revoked',revoked_by='owner@example.com' where id=%s", (asr_review,))
            with pytest.raises(PermissionError, match="PROJECT_L3_EVIDENCE_UNAVAILABLE"):
                ProjectL3EvidenceService(scoped_dsn).prepare(
                    project_id=project_a, video_id=video, transcript_id=transcript, review_version="privacy-v1"
                )

            calls = {"provider": 0}
            selection = ProjectL3ExecutionSelection(
                project_id=project_a, video_id=video, review_id=l3_review, transcript_id=transcript,
                review_version="privacy-v1", evidence_fingerprint=evidence.fingerprint,
                model_id="test-model", model_revision="1", prompt_version="test-prompt",
            )
            with pytest.raises(PermissionError, match="PROJECT_L3_EVIDENCE_UNAVAILABLE"):
                ProjectL3ExecutionService(scoped_dsn).run(
                    selection, provider_factory=lambda: calls.__setitem__("provider", calls["provider"] + 1)  # type: ignore[arg-type]
                )
            assert calls["provider"] == 0
        finally:
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
