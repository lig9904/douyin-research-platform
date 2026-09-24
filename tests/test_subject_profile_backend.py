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
    spec = importlib.util.spec_from_file_location(f"{stem}_profile_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_subject_profile_backend_contract_is_actor_bound() -> None:
    reader = (BACKEND / "get_subject_profiles.py").read_text(encoding="utf-8")
    writer = (BACKEND / "mutate_subject_profile.py").read_text(encoding="utf-8")
    for source in (reader, writer):
        assert "WM_END_USER_EMAIL" in source
        assert "research_subject_profile_version" in source
    assert "status='approved'" in reader
    assert "source_digest" in reader
    assert "include_history" in reader
    assert "create_draft" in writer
    assert "RESEARCH_SUBJECT_PROFILE_ALREADY_APPROVED" in writer
    assert "research_user_action" in writer
    assert (BACKEND / "get_subject_profiles.yaml").exists()
    assert (BACKEND / "mutate_subject_profile.yaml").exists()


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_subject_profile_routes_isolate_projects_drafts_and_replacement(monkeypatch: pytest.MonkeyPatch) -> None:
    assert DSN
    reader, writer = _load("get_subject_profiles"), _load("mutate_subject_profile")
    namespace = f"subject_profile_backend_{uuid4().hex}"
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            admin.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            admin.execute((ROOT / "db/schema.sql").read_text(encoding="utf-8"), prepare=False)
            org = admin.execute(
                "insert into research_organization(slug,name) values ('profile-backend','Profile Backend') returning id"
            ).fetchone()[0]
            project_a = admin.execute(
                """insert into research_project(organization_id,slug,name,status)
                   values (%s,'profile-backend-a','Profile Backend A','active') returning id""", (org,)
            ).fetchone()[0]
            project_b = admin.execute(
                """insert into research_project(organization_id,slug,name,status)
                   values (%s,'profile-backend-b','Profile Backend B','active') returning id""", (org,)
            ).fetchone()[0]
            for actor, role in (("owner@example.com", "owner"), ("admin@example.com", "admin"),
                                ("reader@example.com", "viewer"), ("researcher@example.com", "researcher")):
                admin.execute(
                    "insert into research_project_member(project_id,actor_id,role,status) values (%s,%s,%s,'active')",
                    (project_a, actor, role),
                )
            subject_a = admin.execute(
                "insert into research_subject(project_id,name,subject_type) values (%s,'九九','ip') returning id",
                (project_a,),
            ).fetchone()[0]
            subject_b = admin.execute(
                "insert into research_subject(project_id,name,subject_type) values (%s,'跨项目主体','ip') returning id",
                (project_b,),
            ).fetchone()[0]
            scoped = make_conninfo(DSN, options=f"-c search_path={namespace}")
            monkeypatch.setattr(reader, "_connect", lambda _db: psycopg.connect(scoped, row_factory=dict_row))
            monkeypatch.setattr(writer, "_connect", lambda _db: psycopg.connect(scoped, row_factory=dict_row))
            monkeypatch.setenv("WM_END_USER_EMAIL", "owner@example.com")
            draft_key = str(uuid4())
            draft = writer.main(
                {}, project_id=str(project_a), action="create_draft", idempotency_key=draft_key,
                subject_id=str(subject_a), profile_kind="ip_narrative",
                summary={"target_audience": ["18-35"], "current_facts": ["尚未建号"]},
                rights_status="pending", source_reference="operator://jiujiu/brief-v1", source_digest="a" * 64,
            )
            profile_a = draft["profile"]
            assert profile_a["status"] == "draft"
            assert len(profile_a["content_fingerprint"]) == 64
            replay = writer.main(
                {}, project_id=str(project_a), action="create_draft", idempotency_key=draft_key,
                subject_id=str(subject_a), profile_kind="ip_narrative",
                summary={"target_audience": ["18-35"], "current_facts": ["尚未建号"]},
                rights_status="pending", source_reference="operator://jiujiu/brief-v1", source_digest="a" * 64,
            )
            assert replay["idempotent_replay"] is True
            assert replay["profile"]["id"] == profile_a["id"]
            assert profile_a["version_no"] == 1
            with pytest.raises(ValueError, match="VERSION_NO_MANAGED"):
                writer.main(
                    {}, project_id=str(project_a), action="create_draft", idempotency_key=str(uuid4()),
                    subject_id=str(subject_a), profile_kind="other", version_no=99,
                    summary={"current_facts": ["不允许前端指定版本"]}, rights_status="unknown",
                    source_reference="operator://invalid/version", source_digest="e" * 64,
                )
            with pytest.raises(ValueError, match="SUMMARY_INVALID"):
                writer.main(
                    {}, project_id=str(project_a), action="create_draft", idempotency_key=str(uuid4()),
                    subject_id=str(subject_a), profile_kind="other",
                    summary={"current_facts": [{"hidden": "full script"}]}, rights_status="unknown",
                    source_reference="operator://invalid/nested", source_digest="c" * 64,
                )
            with pytest.raises(ValueError, match="SUMMARY_INVALID"):
                writer.main(
                    {}, project_id=str(project_a), action="create_draft", idempotency_key=str(uuid4()),
                    subject_id=str(subject_a), profile_kind="other",
                    summary={"current_facts": ["x" * 1001]}, rights_status="unknown",
                    source_reference="operator://invalid/long", source_digest="d" * 64,
                )
            assert reader.main({}, project_id=str(project_a), subject_id=str(subject_a))["profiles"] == []
            history = reader.main({}, project_id=str(project_a), subject_id=str(subject_a), include_history=True)
            assert history["can_manage"] is True
            assert history["profiles"][0]["source_digest"] == "a" * 64

            monkeypatch.setenv("WM_END_USER_EMAIL", "reader@example.com")
            reader_view = reader.main({}, project_id=str(project_a), subject_id=str(subject_a), include_history=True)
            assert reader_view == {"project_id": str(project_a), "profiles": [], "can_manage": False}
            with pytest.raises(PermissionError, match="PROFILE_MANAGE_DENIED"):
                writer.main(
                    {}, project_id=str(project_a), action="approve", idempotency_key=str(uuid4()),
                    subject_id=str(subject_a), profile_id=profile_a["id"],
                )
            monkeypatch.setenv("WM_END_USER_EMAIL", "researcher@example.com")
            with pytest.raises(PermissionError, match="PROFILE_MANAGE_DENIED"):
                writer.main(
                    {}, project_id=str(project_a), action="approve", idempotency_key=str(uuid4()),
                    subject_id=str(subject_a), profile_id=profile_a["id"],
                )
            monkeypatch.setenv("WM_END_USER_EMAIL", "admin@example.com")
            with pytest.raises(PermissionError, match="SUBJECT_ACCESS_DENIED"):
                writer.main(
                    {}, project_id=str(project_a), action="approve", idempotency_key=str(uuid4()),
                    subject_id=str(subject_b), profile_id=profile_a["id"],
                )
            approved = writer.main(
                {}, project_id=str(project_a), action="approve", idempotency_key=str(uuid4()),
                subject_id=str(subject_a), profile_id=profile_a["id"],
            )["profile"]
            assert approved["status"] == "approved"
            assert approved["approved_by"] == "admin@example.com"
            assert approved["content_fingerprint"] == profile_a["content_fingerprint"]

            monkeypatch.setenv("WM_END_USER_EMAIL", "reader@example.com")
            approved_view = reader.main({}, project_id=str(project_a), subject_id=str(subject_a), include_history=True)
            assert approved_view["profiles"] == [{
                "id": profile_a["id"], "project_id": str(project_a), "subject_id": str(subject_a),
                "profile_kind": "ip_narrative", "version_no": 1,
                "status": "approved",
                "summary": {"target_audience": ["18-35"], "current_facts": ["尚未建号"]},
                "rights_status": "pending", "content_fingerprint": profile_a["content_fingerprint"],
                "approved_at": approved["approved_at"],
            }]
            assert "source_reference" not in approved_view["profiles"][0]
            assert "source_digest" not in approved_view["profiles"][0]
            monkeypatch.setenv("WM_END_USER_EMAIL", "owner@example.com")
            abandoned = writer.main(
                {}, project_id=str(project_a), action="create_draft", idempotency_key=str(uuid4()),
                subject_id=str(subject_a), profile_kind="other",
                summary={"current_facts": ["来源撤回"]}, rights_status="unknown",
                source_reference="operator://jiujiu/abandoned", source_digest="c" * 64,
            )["profile"]
            revoked_draft = writer.main(
                {}, project_id=str(project_a), action="revoke", idempotency_key=str(uuid4()),
                subject_id=str(subject_a), profile_id=abandoned["id"],
            )["profile"]
            assert revoked_draft["status"] == "revoked"
            assert revoked_draft["approved_by"] is None and revoked_draft["approved_at"] is None
            assert revoked_draft["revoked_at"] is not None
            new_draft = writer.main(
                {}, project_id=str(project_a), action="create_draft", idempotency_key=str(uuid4()),
                subject_id=str(subject_a), profile_kind="ip_narrative",
                summary={"shootable_scenes": ["海边清晨"]}, rights_status="cleared",
                source_reference="operator://jiujiu/brief-v2", source_digest="b" * 64,
            )["profile"]
            assert new_draft["version_no"] == 2
            with pytest.raises(ValueError, match="ALREADY_APPROVED"):
                writer.main(
                    {}, project_id=str(project_a), action="approve", idempotency_key=str(uuid4()),
                    subject_id=str(subject_a), profile_id=new_draft["id"],
                )
            superseded = writer.main(
                {}, project_id=str(project_a), action="supersede", idempotency_key=str(uuid4()),
                subject_id=str(subject_a), profile_id=profile_a["id"],
            )["profile"]
            assert superseded["status"] == "superseded"
            assert superseded["content_fingerprint"] == profile_a["content_fingerprint"]
            assert superseded["approved_by"] == "admin@example.com"
            replacement = writer.main(
                {}, project_id=str(project_a), action="approve", idempotency_key=str(uuid4()),
                subject_id=str(subject_a), profile_id=new_draft["id"],
            )["profile"]
            assert replacement["status"] == "approved"
            monkeypatch.setenv("WM_END_USER_EMAIL", "reader@example.com")
            visible = reader.main({}, project_id=str(project_a), subject_id=str(subject_a))["profiles"]
            assert [profile["id"] for profile in visible] == [new_draft["id"]]
            monkeypatch.setenv("WM_END_USER_EMAIL", "owner@example.com")
            revoked_approved = writer.main(
                {}, project_id=str(project_a), action="revoke", idempotency_key=str(uuid4()),
                subject_id=str(subject_a), profile_id=new_draft["id"],
            )["profile"]
            assert revoked_approved["status"] == "revoked"
            assert revoked_approved["approved_by"] == "owner@example.com"
            assert revoked_approved["approved_at"] is not None and revoked_approved["revoked_at"] is not None
        finally:
            admin.execute("set search_path to public")
            admin.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
