from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "db/schema.sql"
DSN = os.getenv("TEST_DATABASE_URL")


def _module(filename: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _fixture(asset_id: str | None = None, asset_sha: str = "a" * 64):
    importer = _module("import-las-historical-summaries.py", "las_summary_importer")
    summary = json.dumps({"videoDescription": "synthetic public clip", "events": []}, ensure_ascii=False)
    original = {
        "platform_video_id": "7662998648669752619",
        "task_id": "test-task-12345678", "task_status": "COMPLETED",
        "business_code": "0", "final_summary": summary,
        "token_usages": {"prompt_tokens": 100, "completion_tokens": 10},
    }
    manifest = {
        "organization_slug": "las-org", "project_slug": "las-project",
        "authorized_by": "owner@example.com", "authorization_ref": "reviewed-test-user-consent",
        "account_scope": "test-account", "pricing_version": "trial-price-v1",
        "entries": [{
            "platform_video_id": original["platform_video_id"],
            "submit_job_ref": str(uuid4()), "completion_job_ref": str(uuid4()),
            "submit_result": {
                "platform_video_id": original["platform_video_id"],
                "asset_id": asset_id or str(uuid4()), "asset_sha256": asset_sha,
                "task_id": original["task_id"], "model_name": "doubao-seed-2-0-lite-260428",
                "external_paid_calls": 1, "actual_cost": None,
            },
            "completion_result": {
                "platform_video_id": original["platform_video_id"], "task_id": original["task_id"],
                "task_status": "COMPLETED", "business_code": "0",
                "result_sha256": importer._digest(original),
                "final_summary_sha256": hashlib.sha256(summary.encode()).hexdigest(),
                "final_summary_chars": len(summary), "token_usages": original["token_usages"],
            },
            "estimated_cost": "0.123456", "executed_at": "2026-09-27T03:00:00Z",
        }],
    }
    return importer, manifest, original


def test_original_bytes_must_match_reviewed_task_and_summary_hash() -> None:
    importer, manifest, original = _fixture()
    _, results = importer._validated(manifest, [original])
    assert results[0]["summary_sha"] == manifest["entries"][0]["completion_result"]["final_summary_sha256"]
    with pytest.raises(ValueError, match="original LAS bytes disagree"):
        importer._validated(manifest, [{**original, "final_summary": '{"changed":true}'}])
    with pytest.raises(ValueError, match="task identity"):
        importer._validated(manifest, [{**original, "task_id": "other-task-12345678"}])
    with pytest.raises(ValueError, match="exactly one"):
        importer._validated(manifest, [])
    with pytest.raises(ValueError, match="missing or duplicated"):
        importer._validated(manifest, [{**original, "platform_video_id": "9999999999999999999"}])
    broken = {**manifest, "entries": [{**manifest["entries"][0], "submit_result": None}]}
    with pytest.raises(ValueError, match="manifest entry invalid"):
        importer._validated(broken, [original])


def test_result_inputs_are_private_regular_files(tmp_path: Path) -> None:
    importer, _, original = _fixture()
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps(original), encoding="utf-8")
    result_path.chmod(0o644)
    with pytest.raises(ValueError, match="mode 0600"):
        importer._private_json(result_path)
    result_path.chmod(0o600)
    assert importer._private_json(result_path) == original
    link = tmp_path / "result-link.json"
    link.symlink_to(result_path)
    with pytest.raises(ValueError, match="regular private file"):
        importer._private_json(link)


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_historical_summary_import_is_private_idempotent_and_does_not_change_case() -> None:
    assert DSN
    importer, manifest, original = _fixture()
    receipt_importer = _module("import-las-trial-receipts.py", "las_trial_receipt_importer_for_summary")
    namespace = f"las_summary_import_{uuid4().hex}"
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            admin.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            admin.execute(SCHEMA.read_text(encoding="utf-8"), prepare=False)
            org = admin.execute("insert into research_organization(slug,name) values ('las-org','LAS test') returning id").fetchone()[0]
            project = admin.execute(
                "insert into research_project(organization_id,slug,name,status) values(%s,'las-project','LAS project','active') returning id",
                (org,),
            ).fetchone()[0]
            admin.execute(
                "insert into research_project_member(project_id,actor_id,role,status) values(%s,'owner@example.com','owner','active')",
                (project,),
            )
            video = admin.execute(
                "insert into source_video(platform,platform_video_id,title) values('douyin','7662998648669752619','synthetic clip') returning id"
            ).fetchone()[0]
            admin.execute(
                "insert into project_video_inclusion(project_id,video_id,source_type,status) values(%s,%s,'manual','accepted')",
                (project, video),
            )
            sha = "a" * 64
            asset = admin.execute(
                """insert into media_asset(video_id,kind,storage_location,bucket,object_key,content_sha256,size_bytes,content_type)
                   values(%s,'video','s3','test',%s,%s,128,'video/mp4') returning id""",
                (video, f"sha256/aa/{sha}", sha),
            ).fetchone()[0]
            manifest["entries"][0]["submit_result"]["asset_id"] = str(asset)
            config, receipt_entries = receipt_importer._manifest(manifest)
            with psycopg.connect(DSN, options=f"-c search_path={namespace}") as conn:
                assert receipt_importer._import(conn, config, receipt_entries) == (1, 0)
            scoped, results = importer._validated(manifest, [original])
            with psycopg.connect(DSN, options=f"-c search_path={namespace}") as conn:
                assert importer._import(conn, scoped, results) == (1, 0)
            with psycopg.connect(DSN, options=f"-c search_path={namespace}") as conn:
                assert importer._import(conn, scoped, results) == (0, 1)
            row = admin.execute(
                "select final_summary,summary_sha256,provenance from project_las_analysis_result"
            ).fetchone()
            assert row == (original["final_summary"], results[0]["summary_sha"], "provider_machine_only")
            assert admin.execute("select count(*) from project_video_case_review").fetchone()[0] == 0
            assert admin.execute("select count(*) from project_las_supplier_bill_item").fetchone()[0] == 0
            with psycopg.connect(DSN, options=f"-c search_path={namespace}") as conn:
                with pytest.raises(ValueError, match="conflicts with original result"):
                    importer._import(conn, {**scoped, "account_scope": "another-account"}, results)
        finally:
            admin.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
