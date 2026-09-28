from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "windmill/f/content_research/research_dashboard.raw_app/backend"
DSN = os.getenv("TEST_DATABASE_URL")


def _load(stem: str = "project_creative_concepts"):
    path = BACKEND / f"{stem}.py"
    spec = importlib.util.spec_from_file_location(f"{stem}_{uuid4().hex}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _payload(title: str = "候选 A", status: str = "draft") -> dict:
    return {
        "title": title,
        "premise": "测试人物遇到需要判断的问题。",
        "character_choice": "测试人物在两种可行做法中作出选择。",
        "episode_payoff": "选择产生可观察的结果。",
        "evidence_note": "测试依据尚未完成核验。",
        "test_question": "观众能否复述目标、选择和结果？",
        "production_constraints": "仅使用测试占位素材；正式发布权利未核清。",
        "status": status,
    }


def test_creative_concept_contract_is_proposal_only() -> None:
    migration = (ROOT / "db/migrations/044_project_creative_concepts.sql").read_text()
    assert "append-only" in migration
    assert "ready_for_internal_test" in migration
    assert "project_decision_card" not in migration.split("create table if not exists project_creative_concept", 1)[1]
    backend = (BACKEND / "project_creative_concepts.py").read_text()
    assert "WM_END_USER_EMAIL" in backend
    assert "RESEARCH_ACTION_IDEMPOTENCY_CONFLICT" in backend
    assert "for update" in backend
    page = (BACKEND.parent / "ProjectConcepts.tsx").read_text()
    assert "不是行动卡或发布批准" in page
    assert "backend.project_creative_concepts" in page
    assert "选题草稿" in (BACKEND.parent / "AppShell.tsx").read_text()
    release = (ROOT / "scripts/test-server-release.sh").read_text()
    assert "verify_project_creative_contract" in release
    assert "creative_contract=present" in release
    archive = (ROOT / "scripts/test-server-archive-counts.py").read_text()
    assert "'project_creative_044': ('project_creative_concept', 'project_creative_concept_revision')" in archive


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_project_creative_concepts_acl_versioning_and_replay(monkeypatch) -> None:
    assert DSN
    service = _load()
    collaboration = _load("mutate_project_collaboration")
    namespace = f"creative_{uuid4().hex}"
    conninfo = conninfo_to_dict(DSN)
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}, public").format(sql.Identifier(namespace)))
            conn.execute((ROOT / "db/schema.sql").read_text(), prepare=False)
            conn.execute((ROOT / "db/migrations/028_project_decision_loop.sql").read_text(), prepare=False)
            migration = (ROOT / "db/migrations/044_project_creative_concepts.sql").read_text()
            conn.execute(migration, prepare=False)
            conn.execute(migration, prepare=False)
            org = conn.execute("insert into research_organization(slug,name) values('creative-org','创作组织') returning id").fetchone()[0]
            project = conn.execute("insert into research_project(organization_id,slug,name,status) values(%s,'creative-a','项目 A','active') returning id", (org,)).fetchone()[0]
            other = conn.execute("insert into research_project(organization_id,slug,name,status) values(%s,'creative-b','项目 B','active') returning id", (org,)).fetchone()[0]
            conn.execute(
                """insert into research_project_member(project_id,actor_id,role)
                   values (%s,'owner@example.com','owner'),(%s,'coowner@example.com','owner'),
                          (%s,'viewer@example.com','viewer'),
                          (%s,'other@example.com','owner')""",
                (project, project, project, other),
            )
            db = {
                "host": conninfo.get("host") or conn.info.host or "127.0.0.1",
                "port": int(conninfo.get("port") or conn.info.port or 5432),
                "user": conninfo.get("user") or conn.info.user,
                "password": conninfo.get("password", ""),
                "dbname": conninfo.get("dbname") or conn.info.dbname,
                "sslmode": conninfo.get("sslmode", "prefer"),
                "options": f"-c search_path={namespace}",
            }
            monkeypatch.setenv("WM_END_USER_EMAIL", "owner@example.com")
            create_key = str(uuid4())
            created = service.main(db, str(project), "create", _payload(), create_key)
            concept_id = created["concept_id"]
            assert created["version_no"] == 1
            assert service.main(db, str(project), "create", _payload(), create_key)["idempotent_replay"] is True
            assert len(service.main(db, str(project))["concepts"]) == 1
            assert service.main(db, str(project))["concepts"][0]["title"] == "候选 A"
            assert conn.execute("select count(*) from project_decision_card").fetchone()[0] == 0
            with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
                service.main(db, str(project), "create", _payload("不同载荷"), create_key)
            with pytest.raises(PermissionError, match="READ_DENIED"):
                service.main(db, str(other))
            monkeypatch.setenv("WM_END_USER_EMAIL", "other@example.com")
            with pytest.raises(PermissionError, match="CREATIVE_CONCEPT_DENIED"):
                service.main(db, str(other), "history", {"concept_id": concept_id})
            foreign_revise = {
                **_payload("跨项目修订"), "concept_id": concept_id, "expected_version": 1,
            }
            with pytest.raises(PermissionError, match="CREATIVE_CONCEPT_DENIED"):
                service.main(db, str(other), "revise", foreign_revise, str(uuid4()))
            assert conn.execute(
                "select count(*) from project_creative_concept_revision where concept_id=%s",
                (concept_id,),
            ).fetchone()[0] == 1
            monkeypatch.setenv("WM_END_USER_EMAIL", "owner@example.com")
            with pytest.raises(ValueError, match="payload is invalid"):
                service.main(db, str(project), "create", {**_payload(), "fake_approval": True}, str(uuid4()))
            with pytest.raises(ValueError, match="cannot be withdrawn"):
                service.main(db, str(project), "create", _payload(status="withdrawn"), str(uuid4()))
            revise = {**_payload("候选 A 修订", "ready_for_internal_test"), "concept_id": concept_id, "expected_version": 1}
            revision = service.main(db, str(project), "revise", revise, str(uuid4()))
            assert revision["version_no"] == 2
            assert [x["version_no"] for x in service.main(db, str(project), "history", {"concept_id": concept_id})["revisions"]] == [2, 1]
            with pytest.raises(ValueError, match="VERSION_CONFLICT"):
                service.main(db, str(project), "revise", revise, str(uuid4()))
            with pytest.raises(psycopg.Error, match="append-only"):
                conn.execute("update project_creative_concept_revision set title='改写旧稿' where concept_id=%s and version_no=1", (concept_id,))
            with pytest.raises(psycopg.Error, match="out of sequence"):
                conn.execute(
                    """insert into project_creative_concept_revision
                       (concept_id,project_id,version_no,title,premise,character_choice,
                        episode_payoff,evidence_note,test_question,production_constraints,status,recorded_by)
                       values (%s,%s,4,'跳版','事件','选择','回报','依据','问题','边界','draft','owner@example.com')""",
                    (concept_id, project),
                )
            monkeypatch.setenv("WM_END_USER_EMAIL", "viewer@example.com")
            assert service.main(db, str(project))["concepts"][0]["version_no"] == 2
            with pytest.raises(PermissionError, match="WRITE_DENIED"):
                service.main(db, str(project), "create", _payload(), str(uuid4()))
            with pytest.raises(PermissionError, match="WRITE_DENIED"):
                service.main(db, str(project), "revise", {**revise, "expected_version": 2}, str(uuid4()))
            monkeypatch.setenv("WM_END_USER_EMAIL", "owner@example.com")
            withdrawn = {**_payload("候选 A 停用", "withdrawn"), "concept_id": concept_id, "expected_version": 2}
            assert service.main(db, str(project), "revise", withdrawn, str(uuid4()))["version_no"] == 3
            with pytest.raises(ValueError, match="WITHDRAWN"):
                service.main(db, str(project), "revise", {**revise, "expected_version": 3}, str(uuid4()))
            monkeypatch.setenv("WM_END_USER_EMAIL", "coowner@example.com")
            assert collaboration.main(
                db, str(project), "member_revoke", member_email="owner@example.com",
            )["changed"] is True
            assert conn.execute(
                """select status from research_project_member
                   where project_id=%s and actor_id='owner@example.com'
                   order by effective_from desc limit 1""",
                (project,),
            ).fetchone()[0] == "revoked"
            monkeypatch.setenv("WM_END_USER_EMAIL", "owner@example.com")
            with pytest.raises(PermissionError, match="READ_DENIED"):
                service.main(db, str(project))
            with pytest.raises(PermissionError, match="WRITE_DENIED"):
                service.main(db, str(project), "create", _payload(), create_key)
            assert conn.execute(
                "select count(*) from project_creative_concept_revision where concept_id=%s",
                (concept_id,),
            ).fetchone()[0] == 3
        finally:
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
