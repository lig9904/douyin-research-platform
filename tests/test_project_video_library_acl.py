from __future__ import annotations

import importlib.util
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from threading import Event
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from douyin_research.project_analysis.l3 import (
    ProjectL3EvidenceService, ProjectL3ExecutionSelection,
    ProjectL3ExecutionService, ProjectL3ReviewService,
)
from douyin_research.project_analysis.asr_backend import ProjectASRService, ProjectMediaReviewInput
from douyin_research.l2.transcripts import TaskCost
from douyin_research.l3.execution import L3ProviderResponse
from douyin_research.l3.results import L3ResearchResult
from douyin_research.providers.execution_contracts import (
    EXECUTION_CONTRACT_VERSION, L3_SYNC_CAPABILITY, VerifiedExecutionContract,
)


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "windmill/f/content_research/research_dashboard.raw_app/backend/get_video_library.py"
SCHEMA = ROOT / "db/schema.sql"
MIGRATIONS = (
    ROOT / "db/migrations/021_project_account_foundation.sql",
    ROOT / "db/migrations/022_project_task_ownership.sql",
    ROOT / "db/migrations/023_project_video_read_acl.sql",
)
DSN = os.getenv("TEST_DATABASE_URL")


def _load():
    spec = importlib.util.spec_from_file_location("project_video_library_acl", BACKEND)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_video_library_requires_server_identity_and_static_legacy_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load()
    source = BACKEND.read_text(encoding="utf-8")
    metadata = BACKEND.with_suffix(".yaml").read_text(encoding="utf-8")

    assert "WM_END_USER_EMAIL" in source
    assert "project_actor_can_read(%s::uuid, %s)" in source
    assert "project_video_can_read(%s::uuid, %s, %s::uuid)" in source
    assert "project_video_inclusion inclusion_row" in source
    assert "inclusion_row.status <> 'archived'" in source
    assert "Never reuse globally scoped ASR/L3 records in a project view." in source
    assert "legacy_reader_allowlist" not in metadata
    assert "wmill.get_variable(\"f/content_research/research_action_writers\")" in source
    monkeypatch.delenv("WM_END_USER_EMAIL", raising=False)
    with pytest.raises(PermissionError, match="RESEARCH_VIDEO_ACCESS_DENIED"):
        module._actor()


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_project_video_library_blocks_known_cross_project_video_and_preserves_legacy_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert DSN
    module = _load()
    namespace = f"project_video_library_{uuid4().hex}"
    schema = SCHEMA.read_text(encoding="utf-8")
    conninfo = conninfo_to_dict(DSN)
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            conn.execute(schema, prepare=False)
            for migration in MIGRATIONS:
                conn.execute(migration.read_text(encoding="utf-8"), prepare=False)

            organization = conn.execute(
                "insert into research_organization(slug, name) values ('library-org', '视频库组织') returning id"
            ).fetchone()[0]
            project_a = conn.execute(
                """insert into research_project(organization_id, slug, name, status)
                   values (%s, 'project-a', '项目 A', 'active') returning id""",
                (organization,),
            ).fetchone()[0]
            project_b = conn.execute(
                """insert into research_project(organization_id, slug, name, status)
                   values (%s, 'project-b', '项目 B', 'active') returning id""",
                (organization,),
            ).fetchone()[0]
            conn.execute(
                """insert into research_project_member(project_id, actor_id, role)
                   values (%s, 'viewer@example.com', 'viewer')""",
                (project_a,),
            )
            conn.execute(
                """insert into research_project_member(project_id, actor_id, role)
                   values (%s, 'viewer@example.com', 'viewer')""",
                (project_b,),
            )
            conn.execute(
                """insert into research_project_member(project_id, actor_id, role)
                   values (%s, 'owner@example.com', 'owner')""",
                (project_a,),
            )
            video_a = conn.execute(
                """insert into source_video(platform, platform_video_id, title, last_seen_at)
                   values ('douyin', 'library-a', 'A 可见视频', now()) returning id"""
            ).fetchone()[0]
            video_b = conn.execute(
                """insert into source_video(platform, platform_video_id, title, last_seen_at)
                   values ('douyin', 'library-b', 'B 私有视频', now()) returning id"""
            ).fetchone()[0]
            for video_id, project_id, inclusion_source in (
                (video_a, project_a, "manual"),
                (video_b, project_b, "import"),
            ):
                conn.execute(
                    """insert into project_video_inclusion(project_id, video_id, source_type)
                       values (%s, %s, %s)""",
                    (project_id, video_id, inclusion_source),
                )
                conn.execute(
                    """insert into discovery_event(
                         video_id, provider, source_type, source_key, observation_key
                       ) values (%s, 'fixture', %s, %s, %s)""",
                    (video_id, f"private-discovery-{project_id}", f"key-{video_id}", f"observation-{video_id}"),
                )
            conn.execute(
                """insert into project_video_inclusion(project_id, video_id, source_type, source_ref, metadata)
                   values (%s, %s, 'import', 'B-private-ref', '{"owner":"B"}'::jsonb)""",
                (project_b, video_a),
            )
            conn.execute(
                """insert into video_score(video_id, score_type, score, rule_version)
                   values (%s, 'priority', 99, 'private-b-score')""",
                (video_a,),
            )
            collection_id = conn.execute(
                "insert into collection(name, created_by) values ('B 私有收藏', 'owner@example.com') returning id"
            ).fetchone()[0]
            conn.execute(
                "insert into collection_item(collection_id, video_id) values (%s, %s)",
                (collection_id, video_a),
            )

            params = {
                "host": conninfo.get("host") or conn.info.host or "127.0.0.1",
                "port": int(conninfo.get("port") or conn.info.port or 5432),
                "user": conninfo.get("user") or conn.info.user,
                "password": conninfo.get("password", ""),
                "dbname": conninfo.get("dbname") or conn.info.dbname,
                "sslmode": conninfo.get("sslmode", "prefer"),
                "options": f"-c search_path={namespace}",
            }
            project_dsn = make_conninfo(DSN, options=f"-c search_path={namespace}")
            monkeypatch.setenv("WM_END_USER_EMAIL", "VIEWER@example.com")
            monkeypatch.setattr(module, "_get_legacy_allowlist", lambda: "viewer@example.com")
            scoped = module.main(params, project_id=str(project_a), selected_video_id=str(video_a))
            assert [item["id"] for item in scoped["items"]] == [str(video_a)]
            by_platform_id = module.main(params, project_id=str(project_a), query="library-a")
            assert [item["id"] for item in by_platform_id["items"]] == [str(video_a)]
            private_platform_id = module.main(params, project_id=str(project_a), query="library-b")
            assert private_platform_id["total"] == 0
            assert private_platform_id["items"] == []
            conn.execute(
                """update project_video_inclusion
                   set first_seen_at=now()-interval '40 days',
                       last_seen_at=now()-interval '40 days'
                   where project_id=%s""",
                (project_a,),
            )
            stale_by_id = module.main(params, project_id=str(project_a), query="library-a")
            assert [item["id"] for item in stale_by_id["items"]] == [str(video_a)]
            assert module.main(params, project_id=str(project_a), query="A 可见视频")["total"] == 1
            assert module.main(params, project_id=str(project_a), query="library-b")["total"] == 0
            conn.execute(
                "update project_video_inclusion set last_seen_at=now() where project_id=%s",
                (project_a,),
            )
            assert scoped["source_options"] == [{"value": "manual"}]
            item = scoped["items"][0]
            assert item["sources"] == ["manual"]
            assert item["research_level"] is None
            assert item["monitoring_status"] is None
            assert item["monitoring_priority"] is None
            assert item["account_id"] is None
            assert item["priority"] is None
            assert item["collection_count"] is None
            assert item["project_inclusion_status"] == "candidate"
            assert scoped["detail"]["id"] == str(video_a)
            assert scoped["detail"]["sources"] == ["manual"]
            assert scoped["detail"]["project_inclusion_status"] == "candidate"
            for status in ("shortlisted", "rejected"):
                conn.execute(
                    "update project_video_inclusion set status=%s where project_id=%s and video_id=%s",
                    (status, project_a, video_a),
                )
                scoped_status = module.main(params, project_id=str(project_a), selected_video_id=str(video_a))
                assert scoped_status["detail"]["project_inclusion_status"] == status
            assert scoped["detail"]["evidence"] == []
            assert scoped["detail"]["priority"] is None
            assert scoped["detail"]["collection_count"] is None
            assert scoped["detail"]["asr_transcript"] is None
            assert scoped["detail"]["l3_analysis"] is None
            # A real project-local chain must appear only inside its own
            # project, without consulting the legacy transcript/analysis_run.
            conn.execute(
                "update source_video set availability_status='available' where id=%s",
                (video_a,),
            )
            conn.execute(
                "update project_video_inclusion set status='accepted' where project_id=%s and video_id=%s",
                (project_a, video_a),
            )
            accepted = module.main(params, project_id=str(project_a), selected_video_id=str(video_a))
            assert accepted["detail"]["project_inclusion_status"] == "accepted"
            digest = "a" * 64
            asset_id = conn.execute(
                """insert into media_asset(video_id,kind,storage_location,bucket,object_key,
                       content_sha256,size_bytes,content_type)
                   values(%s,'audio','s3','test',%s,%s,64,'audio/wav') returning id""",
                (video_a, f"sha256/{digest[:2]}/{digest}", digest),
            ).fetchone()[0]
            service = ProjectASRService(
                project_dsn, delivery_origin="https://media.example.test",
                variable=lambda _: '["unrelated@example.com"]',
            )
            preview_request = ProjectMediaReviewInput(
                project_a, video_a, asset_id, "v1", {"human": "reviewed"},
            )
            with pytest.raises(PermissionError, match="project owner/admin"):
                service.preview(preview_request)
            monkeypatch.setenv("WM_END_USER_EMAIL", "owner@example.com")
            preview = service.preview(preview_request)
            assert preview["review_status"] == "not_reviewed"
            inventory = service.list_assets(
                project_id=project_a, video_id=video_a, review_version="v1",
            )
            assert [item["asset_id"] for item in inventory["assets"]] == [str(asset_id)]
            assert "object_key" not in str(inventory)
            with pytest.raises(PermissionError):
                service.preview(ProjectMediaReviewInput(
                    project_b, video_a, asset_id, "v1", {"human": "reviewed"},
                ))
            monkeypatch.setenv("WM_END_USER_EMAIL", "viewer@example.com")
            review_id = conn.execute(
                """insert into project_asr_media_review(project_id,video_id,asset_id,
                       review_version,media_fingerprint,asset_manifest_fingerprint,
                       delivery_origin,identity_source,review_statement)
                   values(%s,%s,%s,'v1',%s,%s,'project-preview',
                       'windmill_end_user_email_allowlist_v1','{}') returning id""",
                (project_a, video_a, asset_id, digest, "b" * 64),
            ).fetchone()[0]
            conn.execute(
                "update project_asr_media_review set status='approved',reviewed_by='owner@example.com' where id=%s",
                (review_id,),
            )
            asr_key = "project-library-asr-" + uuid4().hex
            asr_job = conn.execute(
                """insert into project_asr_execution_job(task_key,project_id,video_id,
                       media_review_id,reviewed_asset_id,review_version,media_fingerprint,
                       asset_manifest_fingerprint,provider,model_id,model_revision,
                       engine_version,source_fingerprint,status,cost_currency)
                   values(%s,%s,%s,%s,%s,'v1',%s,%s,'volc','asr','1','wav-v1',%s,
                       'completed','CNY') returning id""",
                (asr_key, project_a, video_a, review_id, asset_id, digest, "b" * 64, digest),
            ).fetchone()[0]
            asr_cost = conn.execute(
                """insert into project_research_task_cost(project_id,video_id,task_key,
                       task_type,task_version,status,input_fingerprint,output_fingerprint,
                       api_cost,asr_cost,llm_cost,cost_currency,cost_basis)
                   values(%s,%s,%s,'asr_transcription','v1','completed',%s,%s,
                       0,0.02,0,'CNY','estimated') returning id""",
                (project_a, video_a, asr_key, digest, digest),
            ).fetchone()[0]
            conn.execute(
                "update project_asr_execution_job set task_cost_id=%s where id=%s",
                (asr_cost, asr_job),
            )
            transcript = conn.execute(
                """insert into project_transcript(project_id,video_id,execution_job_id,
                       media_review_id,asr_provider,model_id,text_content,text_fingerprint,
                       task_cost_id)
                   values(%s,%s,%s,%s,'volc','asr','项目 A 私有转写',%s,%s) returning id""",
                (project_a, video_a, asr_job, review_id, digest, asr_cost),
            ).fetchone()[0]
            conn.execute(
                """insert into video_comment_feature_snapshot(video_id,feature_version,
                       evidence_fingerprint,sampled_comment_count,root_comment_count,
                       sampled_reply_count,source_observation_count,text_present_count,
                       question_text_count,like_known_count,reply_known_count)
                   values(%s,'comment-features-v1',%s,2,2,0,2,2,1,2,0)""",
                (video_a, "e" * 64),
            )
            own_summary = module.main(
                params, project_id=str(project_a), selected_video_id=str(video_a),
            )["detail"]["comment_features"]
            assert own_summary["sampled_comment_count"] == 2
            assert "evidence_fingerprint" not in own_summary
            assert module.main(
                params, project_id=str(project_b), selected_video_id=str(video_a),
            )["detail"]["comment_features"] is None
            evidence = ProjectL3EvidenceService(project_dsn).prepare(
                project_id=project_a, video_id=video_a, transcript_id=transcript,
                review_version="v1",
            )
            assert evidence.bundle["transcript"]["text"] == "项目 A 私有转写"
            assert evidence.bundle["comment_features"]["raw_comment_text_included"] is False
            with pytest.raises(PermissionError, match="PROJECT_L3_EVIDENCE_UNAVAILABLE"):
                ProjectL3EvidenceService(project_dsn).prepare(
                    project_id=project_b, video_id=video_a, transcript_id=transcript,
                    review_version="v1",
                )
            review_service = ProjectL3ReviewService(project_dsn)
            prepared = review_service.prepare(
                actor="owner@example.com", reviewer_allowlist="owner@example.com",
                project_id=project_a, video_id=video_a, transcript_id=transcript,
                review_version="v1",
            )
            assert prepared.fingerprint == evidence.fingerprint
            receipt = review_service.approve(
                actor="owner@example.com", reviewer_allowlist="owner@example.com",
                evidence=prepared,
            )
            assert receipt.idempotent_replay is False
            l3_review = receipt.review_id
            class ProjectFakeProvider:
                provider_name = "fixture"
                pricing_version = "fixture-v1"
                max_retries = 0
                contract = VerifiedExecutionContract(
                    provider="fixture", capability=L3_SYNC_CAPABILITY,
                    contract_version=EXECUTION_CONTRACT_VERSION,
                    model_id="ark", model_revision="1",
                    base_url="https://provider.example.test/v1",
                    auth_scheme="bearer", auth_header_name="Authorization",
                    submit_path="/responses", status_path=None,
                    request_schema_version="l3-request-v1",
                    response_schema_version="l3-response-v1",
                    cost_currency="CNY", polling_billed=False,
                    max_retries=0, timeout_seconds=30,
                    verified_source_fingerprint="f" * 64,
                    production_ready=True,
                )

                def generate(self, request):
                    return L3ProviderResponse(
                        result=L3ResearchResult(
                            model_id=request.model_id, model_revision=request.model_revision,
                            prompt_version=request.prompt_version, schema_version=request.schema_version,
                            input_fingerprint=request.input_fingerprint,
                            evidence_modalities=("metadata", "comments", "transcript"),
                            narrative_structure=("项目 A 私有结论",),
                            hook_functions=("开头线索",), comment_semantics=("样本评论",),
                            case_comparisons=("对照案例",), mechanism_hypotheses=("待验证机制",),
                            ip_fit=("可借鉴方向",), limitations=("样本有限",),
                            privacy_reviewed=True,
                        ),
                        cost=TaskCost(api_cost=Decimal("0"), asr_cost=Decimal("0"),
                                      llm_cost=Decimal("0.03"), currency="CNY", basis="estimated"),
                    )

            execution = ProjectL3ExecutionService(project_dsn).run(
                ProjectL3ExecutionSelection(
                    project_id=project_a, video_id=video_a, review_id=l3_review,
                    transcript_id=transcript, review_version="v1",
                    evidence_fingerprint=evidence.fingerprint, model_id="ark",
                    model_revision="1", prompt_version="p1",
                ),
                provider_factory=ProjectFakeProvider,
            )
            assert execution["status"] == "completed"
            assert execution["external_calls"] == 1
            project_result = module.main(params, project_id=str(project_a), selected_video_id=str(video_a))
            assert project_result["detail"]["asr_transcript"]["text"] == "项目 A 私有转写"
            assert project_result["detail"]["l3_analysis"]["output"]["narrative_structure"] == ["项目 A 私有结论"]
            assert project_result["detail"]["asr_transcript"]["cost"]["total_cost"] == 0.02
            conn.execute(
                "update project_video_inclusion set status='rejected' where project_id=%s and video_id=%s",
                (project_a, video_a),
            )
            rejected_result = module.main(params, project_id=str(project_a), selected_video_id=str(video_a))
            assert rejected_result["detail"]["project_inclusion_status"] == "rejected"
            assert rejected_result["detail"]["asr_transcript"] is None
            assert rejected_result["detail"]["l3_analysis"] is None
            conn.execute(
                "update project_video_inclusion set status='accepted' where project_id=%s and video_id=%s",
                (project_a, video_a),
            )
            # A review revoked concurrently with a real model request must
            # wait for the exact approved call and its result transaction.
            concurrent_evidence = ProjectL3EvidenceService(project_dsn).prepare(
                project_id=project_a, video_id=video_a, transcript_id=transcript,
                review_version="v2",
            )
            concurrent_review = review_service.approve(
                actor="owner@example.com", reviewer_allowlist="owner@example.com",
                evidence=concurrent_evidence,
            ).review_id
            entered, release = Event(), Event()

            class BlockingProvider(ProjectFakeProvider):
                def generate(self, request):
                    entered.set()
                    assert release.wait(5), "test did not release model response"
                    return super().generate(request)

            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    ProjectL3ExecutionService(project_dsn).run,
                    ProjectL3ExecutionSelection(
                        project_id=project_a, video_id=video_a, review_id=concurrent_review,
                        transcript_id=transcript, review_version="v2",
                        evidence_fingerprint=concurrent_evidence.fingerprint,
                        model_id="ark", model_revision="1", prompt_version="p1",
                    ), provider_factory=BlockingProvider,
                )
                assert entered.wait(5), "model request was not entered"
                try:
                    with psycopg.connect(project_dsn) as revoker:
                        revoker.execute("set lock_timeout='100ms'")
                        with pytest.raises(psycopg.errors.LockNotAvailable):
                            revoker.execute(
                                "update project_l3_privacy_review set status='revoked',revoked_by='owner@example.com' where id=%s",
                                (concurrent_review,),
                            )
                finally:
                    release.set()
                assert future.result(timeout=5)["status"] == "completed"
            conn.execute(
                "update project_l3_privacy_review set status='revoked',revoked_by='owner@example.com' where id=%s",
                (concurrent_review,),
            )
            other_project = module.main(params, project_id=str(project_b), selected_video_id=str(video_a))
            assert other_project["detail"]["asr_transcript"] is None
            assert other_project["detail"]["l3_analysis"] is None
            conn.execute(
                "update project_asr_media_review set status='revoked',revoked_by='owner@example.com' where id=%s",
                (review_id,),
            )
            revoked = module.main(params, project_id=str(project_a), selected_video_id=str(video_a))
            assert revoked["detail"]["asr_transcript"] is None
            assert revoked["detail"]["l3_analysis"] is None
            assert "B-private-ref" not in str(scoped)
            assert "private-b-score" not in str(scoped)
            with pytest.raises(PermissionError, match="RESEARCH_VIDEO_ACCESS_DENIED"):
                module.main(params, project_id=str(project_a), priority_min=1)
            with pytest.raises(PermissionError, match="RESEARCH_VIDEO_ACCESS_DENIED"):
                module.main(params, project_id=str(project_a), sort="priority_desc")
            with pytest.raises(PermissionError, match="RESEARCH_VIDEO_ACCESS_DENIED"):
                module.main(params, project_id=str(project_a), selected_video_id=str(video_b))
            monkeypatch.setattr(module, "_get_legacy_allowlist", lambda: "")
            with pytest.raises(PermissionError, match="RESEARCH_VIDEO_ACCESS_DENIED"):
                module.main(params)
            monkeypatch.setattr(module, "_get_legacy_allowlist", lambda: (_ for _ in ()).throw(RuntimeError("missing variable")))
            with pytest.raises(PermissionError, match="RESEARCH_VIDEO_ACCESS_DENIED"):
                module.main(params)
            monkeypatch.setattr(module, "_get_legacy_allowlist", lambda: "viewer@example.com")
            legacy = module.main(params)
            assert {item["id"] for item in legacy["items"]} == {str(video_a), str(video_b)}
        finally:
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
