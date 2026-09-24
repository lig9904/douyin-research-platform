from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "windmill/f/content_research/research_dashboard.raw_app/backend"
DSN = os.getenv("TEST_DATABASE_URL")


def _load(stem: str):
    path = BACKEND / f"{stem}.py"
    spec = importlib.util.spec_from_file_location(f"{stem}_subject_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_project_subject_routes_are_member_bound_and_audit_manual_correction(monkeypatch: pytest.MonkeyPatch) -> None:
    assert DSN
    writer = _load("mutate_project_subject")
    reader = _load("get_project_subjects")
    namespace = f"project_subject_{uuid4().hex}"
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            admin.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            admin.execute((ROOT / "db/schema.sql").read_text(encoding="utf-8"), prepare=False)
            org = admin.execute("insert into research_organization(slug,name) values ('subject-ui','Subject UI') returning id").fetchone()[0]
            project = admin.execute(
                """insert into research_project(organization_id,slug,name,status)
                   values (%s,'subject-ui-project','Subject UI Project','active') returning id""", (org,)
            ).fetchone()[0]
            admin.execute(
                """insert into research_project_member(project_id,actor_id,role,status)
                   values (%s,'owner@example.com','owner','active')""", (project,)
            )
            scoped = make_conninfo(DSN, options=f"-c search_path={namespace}")
            monkeypatch.setattr(writer, "_connect", lambda _db: psycopg.connect(scoped, row_factory=dict_row))
            monkeypatch.setattr(reader, "_connect", lambda _db: psycopg.connect(scoped, row_factory=dict_row))
            monkeypatch.setenv("WM_END_USER_EMAIL", "owner@example.com")
            created = writer.main(
                {}, project_id=str(project), action="create_subject", idempotency_key=str(uuid4()),
                name="渔岛", subject_type="destination", aliases=["渔岛温泉"],
                geographic_contexts=["秦皇岛"], exclusions=["招聘"],
            )
            subject = created["subject_id"]
            listed = reader.main({}, project_id=str(project))
            assert listed["subjects"][0]["name"] == "渔岛"
            assert {term["term_type"] for term in listed["subjects"][0]["terms"]} == {
                "alias", "geographic_context", "exclusion",
            }
            admin.execute(
                """insert into source_video(platform,platform_video_id,title,availability_status)
                   values ('douyin','87654321234567890','渔岛温泉','available') returning id"""
            )
            video = admin.execute(
                "select id from source_video where platform_video_id='87654321234567890'"
            ).fetchone()[0]
            admin.execute(
                """insert into project_video_inclusion(project_id,video_id,source_type,status)
                   values (%s,%s,'manual','accepted')""", (project, video)
            )
            run = admin.execute(
                """insert into pipeline_run(run_type,platform,project_id)
                   values ('subject_test','douyin',%s) returning id""", (project,)
            ).fetchone()[0]
            admin.execute(
                """insert into project_video_subject_relevance(
                     project_id,video_id,subject_id,run_id,decision,decision_source,rule_version
                   ) values (%s,%s,%s,%s,'pending','rule','subject-relevance-v1.0.0')""",
                (project, video, subject, run),
            )
            result = writer.main(
                {}, project_id=str(project), action="correct_relevance", idempotency_key=str(uuid4()),
                subject_id=subject, video_id=str(video), decision="relevant", reason="主体与地域均明确命中",
            )
            assert result["decision"] == "relevant"
            audit = admin.execute(
                """select event_type,actor,prior_decision,decision,reason,run_id
                   from project_video_subject_relevance_audit"""
            ).fetchone()
            assert audit == ("manual_override", "owner@example.com", "pending", "relevant", "主体与地域均明确命中", run)
            monkeypatch.setenv("WM_END_USER_EMAIL", "viewer@example.com")
            with pytest.raises(PermissionError, match="RESEARCH_PROJECT_MANAGE_DENIED"):
                writer.main(
                    {}, project_id=str(project), action="update_terms", idempotency_key=str(uuid4()),
                    subject_id=subject, aliases=["无权限"], geographic_contexts=[], exclusions=[],
                )
        finally:
            admin.execute("set search_path to public")
            admin.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
