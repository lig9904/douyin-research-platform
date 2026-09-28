from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]
IMPORTER = ROOT / "scripts/import-las-trial-receipts.py"
SCHEMA = ROOT / "db/schema.sql"
DSN = os.getenv("TEST_DATABASE_URL")


def _module():
    spec = importlib.util.spec_from_file_location("las_trial_importer", IMPORTER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _manifest(asset_id: str, sha: str):
    return {
        "organization_slug": "las-org", "project_slug": "las-project",
        "authorized_by": "owner@example.com",
        "authorization_ref": "reviewed-test-user-consent",
        "account_scope": "test-account", "pricing_version": "trial-price-v1",
        "entries": [{
            "platform_video_id": "las-video-1",
            "submit_job_ref": str(uuid4()), "completion_job_ref": str(uuid4()),
            "submit_result": {
                "platform_video_id": "las-video-1", "asset_id": asset_id,
                "asset_sha256": sha, "task_id": "test-task-12345678",
                "model_name": "doubao-seed-2-0-lite-260428",
                "external_paid_calls": 1, "actual_cost": None,
            },
            "completion_result": {
                "platform_video_id": "las-video-1", "task_id": "test-task-12345678",
                "task_status": "COMPLETED", "business_code": "0",
                "final_summary": '{"videoDescription":"synthetic public clip"}',
                "token_usages": {"prompt_tokens": 100, "completion_tokens": 10},
            },
            "estimated_cost": "0.123456", "executed_at": "2026-09-27T03:00:00Z",
        }],
    }


def test_las_import_rejects_wrong_task_video_and_cost_claim() -> None:
    importer = _module()
    manifest = _manifest(str(uuid4()), "a" * 64)
    config, entries = importer._manifest(manifest)
    assert config["project_slug"] == "las-project"
    assert entries[0]["estimate"] == importer.Decimal("0.123456")
    manifest["entries"][0]["completion_result"]["task_id"] = "other-task-12345678"
    with pytest.raises(ValueError, match="task identity mismatch"):
        importer._manifest(manifest)
    manifest["entries"][0]["completion_result"]["task_id"] = "test-task-12345678"
    manifest["entries"][0]["submit_result"]["actual_cost"] = 0
    with pytest.raises(ValueError, match="cost claim invalid"):
        importer._manifest(manifest)


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_las_import_is_project_scoped_idempotent_and_never_imports_a_bill() -> None:
    assert DSN
    importer = _module()
    namespace = f"las_trial_import_{uuid4().hex}"
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            admin.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            admin.execute(SCHEMA.read_text(encoding="utf-8"), prepare=False)
            org = admin.execute(
                "insert into research_organization(slug,name) values ('las-org','LAS test') returning id"
            ).fetchone()[0]
            project = admin.execute(
                """insert into research_project(organization_id,slug,name,status)
                   values(%s,'las-project','LAS project','active') returning id""",
                (org,),
            ).fetchone()[0]
            admin.execute(
                """insert into research_project_member(project_id,actor_id,role,status)
                   values(%s,'owner@example.com','owner','active')""",
                (project,),
            )
            video = admin.execute(
                """insert into source_video(platform,platform_video_id,title)
                   values('douyin','las-video-1','synthetic clip') returning id"""
            ).fetchone()[0]
            admin.execute(
                """insert into project_video_inclusion(project_id,video_id,source_type,status)
                   values(%s,%s,'manual','accepted')""",
                (project, video),
            )
            sha = "c" * 64
            asset = admin.execute(
                """insert into media_asset(video_id,kind,storage_location,bucket,
                     object_key,content_sha256,size_bytes,content_type)
                   values(%s,'video','s3','test',%s,%s,128,'video/mp4') returning id""",
                (video, f"sha256/cc/{sha}", sha),
            ).fetchone()[0]
            config, entries = importer._manifest(_manifest(str(asset), sha))
            with psycopg.connect(DSN, options=f"-c search_path={namespace}") as conn:
                assert importer._import(conn, config, entries) == (1, 0)
            with psycopg.connect(DSN, options=f"-c search_path={namespace}") as conn:
                assert importer._import(conn, config, entries) == (0, 1)
            assert admin.execute("select count(*) from project_las_analysis_attempt").fetchone()[0] == 1
            assert admin.execute("select count(*) from project_las_analysis_receipt").fetchone()[0] == 1
            assert admin.execute("select count(*) from project_las_supplier_bill_item").fetchone()[0] == 0
            altered_config = {**config, "account_scope": "another-billing-account"}
            with psycopg.connect(DSN, options=f"-c search_path={namespace}") as conn:
                with pytest.raises(ValueError, match="conflicts with import"):
                    importer._import(conn, altered_config, entries)
            altered_entries = [{**entries[0], "completion_job": str(uuid4())}]
            with psycopg.connect(DSN, options=f"-c search_path={namespace}") as conn:
                with pytest.raises(ValueError, match="conflicts with import"):
                    importer._import(conn, config, altered_entries)
            second_org = admin.execute(
                "insert into research_organization(slug,name) values ('another-org','Second org') returning id"
            ).fetchone()[0]
            second_project = admin.execute(
                """insert into research_project(organization_id,slug,name,status)
                   values(%s,'las-project','Same slug','active') returning id""",
                (second_org,),
            ).fetchone()[0]
            admin.execute(
                """insert into research_project_member(project_id,actor_id,role,status)
                   values(%s,'owner@example.com','owner','active')""",
                (second_project,),
            )
            admin.execute(
                """insert into project_video_inclusion(project_id,video_id,source_type,status)
                   values(%s,%s,'manual','accepted')""",
                (second_project, video),
            )
            second_config = {**config, "organization_slug": "another-org"}
            with psycopg.connect(DSN, options=f"-c search_path={namespace}") as conn:
                with pytest.raises(psycopg.errors.UniqueViolation):
                    importer._import(conn, second_config, entries)
            assert admin.execute(
                "select count(*) from project_las_analysis_receipt where project_id=%s", (second_project,)
            ).fetchone()[0] == 0
            admin.execute(
                """insert into research_project_member(project_id,actor_id,role,status,effective_from)
                   values(%s,'owner@example.com','viewer','active',clock_timestamp())""",
                (project,),
            )
            with psycopg.connect(DSN, options=f"-c search_path={namespace}") as conn:
                with pytest.raises(ValueError, match="not an active project owner/admin"):
                    importer._import(conn, config, entries)
            admin.execute(
                """insert into research_project_member(project_id,actor_id,role,status,effective_from)
                   values(%s,'owner@example.com','owner','active',clock_timestamp())""",
                (project,),
            )
            admin.execute(
                "update project_video_inclusion set status='archived' where project_id=%s and video_id=%s",
                (project, video),
            )
            with psycopg.connect(DSN, options=f"-c search_path={namespace}") as conn:
                with pytest.raises(ValueError, match="accepted project video"):
                    importer._import(conn, config, entries)
        finally:
            admin.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
