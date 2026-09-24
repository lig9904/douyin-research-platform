from __future__ import annotations

import hashlib
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql


ROOT = Path(__file__).parents[1]
SCHEMA = ROOT / "db/schema.sql"
MIGRATION = ROOT / "db/migrations/030_subject_profile_version.sql"
DSN = os.getenv("TEST_DATABASE_URL")


def test_subject_profile_schema_contract_is_versioned_and_local() -> None:
    migration = MIGRATION.read_text(encoding="utf-8")
    schema = SCHEMA.read_text(encoding="utf-8")
    for source in (migration, schema):
        assert "research_subject_profile_version" in source
        assert "references research_subject(id, project_id)" in source
        assert "ip_narrative" in source
        assert "destination_experience" in source
        assert "activity_conversion" in source
        assert "content_fingerprint" in source
        assert "source_digest ~ '^[0-9a-f]{64}$'" in source
        assert "enforce_research_subject_profile_version" in source
        assert "reject_research_subject_profile_version_delete" in source
        assert "target_audience" in source
        assert "forbidden_expressions" in source
    assert "project_decision_card" not in migration
    assert "l3_" not in migration.lower()


def _fingerprint(*, kind: str, summary: str, rights: str, reference: str, digest: str) -> str:
    material = "\n".join((kind, summary, rights, reference, digest))
    return hashlib.sha256(material.encode()).hexdigest()


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_030_upgrades_the_029_schema_without_a_fresh_bootstrap() -> None:
    assert DSN
    namespace = f"subject_profile_upgrade_{uuid4().hex}"
    marker = "-- Fresh-volume bootstrap parity with migration 030."
    full_schema = SCHEMA.read_text(encoding="utf-8")
    assert full_schema.count(marker) == 1
    baseline_029 = full_schema.split(marker, 1)[0]
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            admin.execute(sql.SQL("set search_path to {}, public").format(sql.Identifier(namespace)))
            admin.execute(baseline_029, prepare=False)
            assert admin.execute("select to_regclass('project_video_subject_score') is not null").fetchone()[0]
            assert admin.execute("select to_regclass('research_subject_profile_version') is null").fetchone()[0]
            admin.execute(MIGRATION.read_text(encoding="utf-8"), prepare=False)
            assert admin.execute("select to_regclass('research_subject_profile_version') is not null").fetchone()[0]
            assert admin.execute(
                "select count(*) from pg_trigger where tgrelid='research_subject_profile_version'::regclass and not tgisinternal"
            ).fetchone()[0] == 2
            assert admin.execute(
                "select count(*) from pg_constraint where conrelid='research_subject_profile_version'::regclass and contype='f' and confdeltype='r'"
            ).fetchone()[0] == 2
        finally:
            admin.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_subject_profile_lifecycle_is_scoped_and_approved_content_is_immutable() -> None:
    assert DSN
    namespace = f"subject_profile_{uuid4().hex}"
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            admin.execute(sql.SQL("set search_path to {}, public").format(sql.Identifier(namespace)))
            admin.execute(SCHEMA.read_text(encoding="utf-8"), prepare=False)
            admin.execute(MIGRATION.read_text(encoding="utf-8"), prepare=False)
            org = admin.execute(
                "insert into research_organization(slug,name) values ('profile-org','Profile Org') returning id"
            ).fetchone()[0]
            project_a = admin.execute(
                """insert into research_project(organization_id,slug,name,status)
                   values (%s,'profile-a','Profile A','active') returning id""",
                (org,),
            ).fetchone()[0]
            project_b = admin.execute(
                """insert into research_project(organization_id,slug,name,status)
                   values (%s,'profile-b','Profile B','active') returning id""",
                (org,),
            ).fetchone()[0]
            subject_a = admin.execute(
                """insert into research_subject(project_id,name,subject_type)
                   values (%s,'九九','ip') returning id""",
                (project_a,),
            ).fetchone()[0]
            subject_b = admin.execute(
                """insert into research_subject(project_id,name,subject_type)
                   values (%s,'另一主体','ip') returning id""",
                (project_b,),
            ).fetchone()[0]
            digest = "a" * 64
            summary = {"target_audience": ["18-35"], "current_facts": ["尚未建号"]}
            profile = admin.execute(
                """insert into research_subject_profile_version(
                     project_id,subject_id,profile_kind,version_no,summary,rights_status,source_reference,source_digest
                   ) values (%s,%s,'ip_narrative',1,%s,'pending','operator://brief/1',%s)
                   returning id,content_fingerprint,status""",
                (project_a, subject_a, psycopg.types.json.Jsonb(summary), digest),
            ).fetchone()
            expected = _fingerprint(
                kind="ip_narrative",
                summary='{"current_facts": ["尚未建号"], "target_audience": ["18-35"]}',
                rights="pending",
                reference="operator://brief/1",
                digest=digest,
            )
            assert profile[1] == expected
            assert profile[2] == "draft"

            draft_to_revoke = admin.execute(
                """insert into research_subject_profile_version(
                     project_id,subject_id,profile_kind,version_no,summary,rights_status,source_reference,source_digest
                   ) values (%s,%s,'other',1,'{"current_facts":["待核对"]}','unknown','operator://draft-to-revoke',%s) returning id""",
                (project_a, subject_a, "c" * 64),
            ).fetchone()[0]
            revoked_draft = admin.execute(
                """update research_subject_profile_version set status='revoked' where id=%s
                   returning approved_by,approved_at,revoked_at""",
                (draft_to_revoke,),
            ).fetchone()
            assert revoked_draft[0] is None and revoked_draft[1] is None and revoked_draft[2] is not None

            with pytest.raises(psycopg.errors.CheckViolation):
                admin.execute(
                    """insert into research_subject_profile_version(
                         project_id,subject_id,profile_kind,version_no,summary,rights_status,source_reference,source_digest
                       ) values (%s,%s,'other',2,'{}','unknown','operator://empty',%s)""",
                    (project_a, subject_a, digest),
                )

            with pytest.raises(psycopg.errors.CheckViolation):
                admin.execute(
                    """insert into research_subject_profile_version(
                         project_id,subject_id,profile_kind,version_no,summary,rights_status,source_reference,source_digest
                       ) values (%s,%s,'other',1,%s,'unknown','operator://invalid',%s)""",
                    (project_a, subject_a, psycopg.types.json.Jsonb({"full_script": "not allowed"}), digest),
                )
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                admin.execute(
                    """insert into research_subject_profile_version(
                         project_id,subject_id,profile_kind,version_no,summary,rights_status,source_reference,source_digest
                       ) values (%s,%s,'other',1,'{"current_facts":["跨项目"]}','unknown','operator://cross-project',%s)""",
                    (project_a, subject_b, digest),
                )

            with pytest.raises(psycopg.errors.RaiseException, match="must begin as draft"):
                admin.execute(
                    """insert into research_subject_profile_version(
                         project_id,subject_id,profile_kind,version_no,status,summary,rights_status,source_reference,source_digest
                       ) values (%s,%s,'other',1,'approved','{"current_facts":["拒绝跳级"]}','unknown','operator://no-draft',%s)""",
                    (project_a, subject_a, digest),
                )
            with pytest.raises(psycopg.errors.RaiseException, match="requires approved_by"):
                admin.execute(
                    "update research_subject_profile_version set status='approved' where id=%s", (profile[0],)
                )
            admin.execute(
                """update research_subject_profile_version
                   set status='approved', approved_by='owner@example.com' where id=%s""",
                (profile[0],),
            )
            approved = admin.execute(
                "select content_fingerprint,approved_by,approved_at from research_subject_profile_version where id=%s",
                (profile[0],),
            ).fetchone()
            assert approved[0] == expected
            assert approved[1] == "owner@example.com"
            assert approved[2] is not None

            with pytest.raises(psycopg.errors.RaiseException, match="approved research_subject_profile_version"):
                admin.execute(
                    """update research_subject_profile_version
                       set summary=%s where id=%s""",
                    (psycopg.types.json.Jsonb({"target_audience": ["所有人"]}), profile[0]),
                )
            with pytest.raises(psycopg.errors.RaiseException, match="only become superseded or revoked"):
                admin.execute(
                    "update research_subject_profile_version set status='approved' where id=%s", (profile[0],)
                )
            with pytest.raises(psycopg.errors.RaiseException, match="must be revoked, not deleted"):
                admin.execute("delete from research_subject_profile_version where id=%s", (profile[0],))

            next_profile = admin.execute(
                """insert into research_subject_profile_version(
                     project_id,subject_id,profile_kind,version_no,summary,rights_status,source_reference,source_digest
                   ) values (%s,%s,'ip_narrative',2,'{"current_facts":["新版本"]}','cleared','operator://brief/2',%s) returning id""",
                (project_a, subject_a, "b" * 64),
            ).fetchone()[0]
            with pytest.raises(psycopg.errors.UniqueViolation):
                admin.execute(
                    """update research_subject_profile_version
                       set status='approved',approved_by='owner@example.com' where id=%s""",
                    (next_profile,),
                )
            admin.execute(
                "update research_subject_profile_version set status='superseded' where id=%s", (profile[0],)
            )
            admin.execute(
                """update research_subject_profile_version
                   set status='approved',approved_by='owner@example.com' where id=%s""",
                (next_profile,),
            )
            assert admin.execute(
                "select count(*) from research_subject_profile_version where project_id=%s and subject_id=%s and status='approved'",
                (project_a, subject_a),
            ).fetchone()[0] == 1
            revoked_approved = admin.execute(
                """update research_subject_profile_version set status='revoked' where id=%s
                   returning approved_by,approved_at,revoked_at""",
                (next_profile,),
            ).fetchone()
            assert revoked_approved[0] == "owner@example.com"
            assert revoked_approved[1] is not None and revoked_approved[2] is not None
        finally:
            admin.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
