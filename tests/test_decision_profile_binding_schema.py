from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "db/schema.sql"
MIGRATION = ROOT / "db/migrations/031_decision_card_profile_binding.sql"
DSN = os.getenv("TEST_DATABASE_URL")


def test_decision_profile_binding_contract_is_immutable_and_rights_aware() -> None:
    migration = MIGRATION.read_text(encoding="utf-8")
    schema = SCHEMA.read_text(encoding="utf-8")
    for source in (migration, schema):
        assert "project_decision_card_profile_binding" in source
        assert "rights_status_at_binding" in source
        assert "profile_content_fingerprint" in source
        assert "profile_source_digest" in source
        assert "approved, rights-cleared" in source
        assert "deferrable initially deferred" in source
        assert "for update" in source
        assert "is immutable" in source
    assert "l3_" not in migration.lower()
    assert "project_video_share_grant" not in migration


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_031_preserves_legacy_cards_and_requires_a_real_cleared_profile_for_new_adoption() -> None:
    assert DSN
    namespace = f"decision_profile_binding_{uuid4().hex}"
    marker = "-- Fresh-volume bootstrap parity with migration 031."
    baseline_030 = SCHEMA.read_text(encoding="utf-8").split(marker, 1)[0]
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}, public").format(sql.Identifier(namespace)))
            conn.execute(baseline_030, prepare=False)
            org = conn.execute(
                "insert into research_organization(slug,name) values('binding-org','绑定组织') returning id"
            ).fetchone()[0]
            project = conn.execute(
                """insert into research_project(organization_id,slug,name,status)
                   values(%s,'binding-project','绑定项目','active') returning id""",
                (org,),
            ).fetchone()[0]
            conn.execute(
                """insert into research_project_member(project_id,actor_id,role)
                   values(%s,'owner@example.com','owner'),(%s,'researcher@example.com','researcher')""",
                (project, project),
            )
            video = conn.execute(
                """insert into source_video(platform,platform_video_id,title)
                   values('douyin','3111111111111111111','本地参考') returning id"""
            ).fetchone()[0]
            conn.execute(
                """insert into project_video_inclusion(project_id,video_id,source_type,source_ref,status)
                   values(%s,%s,'manual','local','accepted')""",
                (project, video),
            )
            subject = conn.execute(
                """insert into research_subject(project_id,name,subject_type)
                   values(%s,'主体 A','ip') returning id""",
                (project,),
            ).fetchone()[0]
            legacy = conn.execute(
                """insert into project_decision_card(
                     project_id,source_video_id,hypothesis,reference_point,adaptation_difference,
                     owner_actor,decision,created_by
                   ) values(%s,%s,'旧卡假设','旧卡参考','旧卡差异','owner@example.com','adopt','owner@example.com')
                   returning id""",
                (project, video),
            ).fetchone()[0]

            conn.execute(MIGRATION.read_text(encoding="utf-8"), prepare=False)
            assert conn.execute(
                "select profile_binding_required_at is null from project_decision_card where id=%s", (legacy,)
            ).fetchone()[0] is True
            with pytest.raises(psycopg.Error, match="legacy adopt.*immutable"):
                conn.execute("update project_decision_card set hypothesis='伪装成新行动' where id=%s", (legacy,))
            with pytest.raises(psycopg.Error, match="legacy adopt.*immutable"):
                conn.execute("update project_decision_card set subject_id=%s where id=%s", (subject, legacy))
            conn.execute("update project_decision_card set status='archived' where id=%s", (legacy,))

            profile = conn.execute(
                """insert into research_subject_profile_version(
                     project_id,subject_id,profile_kind,version_no,summary,rights_status,source_reference,source_digest
                   ) values(%s,%s,'ip_narrative',1,'{"current_facts":["可拍"],"target_audience":["18-35"]}',
                            'cleared','operator://subject/a','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')
                   returning id""",
                (project, subject),
            ).fetchone()[0]
            conn.execute(
                """update research_subject_profile_version
                   set status='approved', approved_by='owner@example.com' where id=%s""",
                (profile,),
            )

            with pytest.raises(psycopg.Error, match="requires a subject profile binding"):
                conn.execute(
                    """insert into project_decision_card(
                         project_id,source_video_id,hypothesis,reference_point,adaptation_difference,
                         owner_actor,decision,created_by
                       ) values(%s,%s,'无主体','参考','差异','owner@example.com','adopt','owner@example.com')""",
                    (project, video),
                )

            with conn.transaction():
                card = conn.execute(
                    """insert into project_decision_card(
                         project_id,source_video_id,subject_id,hypothesis,reference_point,adaptation_difference,
                         owner_actor,decision,created_by
                       ) values(%s,%s,%s,'新卡假设','新卡参考','新卡差异','owner@example.com','adopt','owner@example.com')
                       returning id""",
                    (project, video, subject),
                ).fetchone()[0]
                binding = conn.execute(
                    """insert into project_decision_card_profile_binding(
                         project_id,decision_card_id,subject_id,profile_id,profile_content_fingerprint,profile_source_digest,
                         profile_kind,profile_version_no,rights_status_at_binding,profile_approved_at,bound_by
                       ) values(%s,%s,%s,%s,%s,%s,'other',999,'cleared',now(),'owner@example.com')
                       returning profile_content_fingerprint,profile_source_digest,profile_kind,rights_status_at_binding,profile_version_no""",
                    (project, card, subject, profile, "b" * 64, "b" * 64),
                ).fetchone()
            assert binding[0] != "b" * 64
            assert binding[1:] == ("a" * 64, "ip_narrative", "cleared", 1)

            def rejected_binding(candidate_profile, *, candidate_subject=subject, actor="owner@example.com", reason: str):
                with pytest.raises(psycopg.Error, match=reason):
                    with conn.transaction():
                        candidate = conn.execute(
                            """insert into project_decision_card(
                                 project_id,source_video_id,subject_id,hypothesis,reference_point,
                                 adaptation_difference,owner_actor,decision,created_by)
                               values(%s,%s,%s,'待拒绝','参考','差异','owner@example.com','adopt','owner@example.com')
                               returning id""",
                            (project, video, candidate_subject),
                        ).fetchone()[0]
                        conn.execute(
                            """insert into project_decision_card_profile_binding(
                                 project_id,decision_card_id,subject_id,profile_id,bound_by)
                               values(%s,%s,%s,%s,%s)""",
                            (project, candidate, candidate_subject, candidate_profile, actor),
                        )

            rejected_binding(profile, actor="researcher@example.com", reason="owner or admin")
            pending = conn.execute(
                """insert into research_subject_profile_version(
                     project_id,subject_id,profile_kind,version_no,summary,rights_status,source_reference,source_digest)
                   values(%s,%s,'ip_narrative',2,'{"current_facts":["待核准"]}','pending','operator://pending',%s)
                   returning id""",
                (project, subject, "c" * 64),
            ).fetchone()[0]
            rejected_binding(pending, reason="approved, rights-cleared")
            conn.execute("update research_subject_profile_version set status='revoked' where id=%s", (pending,))
            rejected_binding(pending, reason="approved, rights-cleared")
            other_subject = conn.execute(
                "insert into research_subject(project_id,name,subject_type) values(%s,'主体 B','ip') returning id",
                (project,),
            ).fetchone()[0]
            rejected_binding(profile, candidate_subject=other_subject, reason="approved, rights-cleared")
            other_project = conn.execute(
                "insert into research_project(organization_id,slug,name,status) values(%s,'binding-other','其他项目','active') returning id",
                (org,),
            ).fetchone()[0]
            conn.execute("insert into research_project_member(project_id,actor_id,role) values(%s,'owner@example.com','owner')", (other_project,))
            conn.execute("insert into project_video_inclusion(project_id,video_id,source_type,source_ref,status) values(%s,%s,'manual','other','accepted')", (other_project, video))
            other_project_subject = conn.execute(
                "insert into research_subject(project_id,name,subject_type) values(%s,'他项目主体','ip') returning id",
                (other_project,),
            ).fetchone()[0]
            with pytest.raises(psycopg.Error, match="approved, rights-cleared"):
                with conn.transaction():
                    other_card = conn.execute(
                        """insert into project_decision_card(
                             project_id,source_video_id,subject_id,hypothesis,reference_point,
                             adaptation_difference,owner_actor,decision,created_by)
                           values(%s,%s,%s,'他项目卡','参考','差异','owner@example.com','adopt','owner@example.com') returning id""",
                        (other_project, video, other_project_subject),
                    ).fetchone()[0]
                    conn.execute(
                        """insert into project_decision_card_profile_binding(
                             project_id,decision_card_id,subject_id,profile_id,bound_by)
                           values(%s,%s,%s,%s,'owner@example.com')""",
                        (other_project, other_card, other_project_subject, profile),
                    )
            conn.execute("update research_subject_profile_version set status='superseded' where id=%s", (profile,))
            rejected_binding(profile, reason="approved, rights-cleared")

            with pytest.raises(psycopg.Error, match="immutable"):
                conn.execute(
                    """update project_decision_card_profile_binding
                       set profile_content_fingerprint=%s where decision_card_id=%s""",
                    ("b" * 64, card),
                )
            with pytest.raises(psycopg.Error, match="immutable"):
                conn.execute(
                    "delete from project_decision_card_profile_binding where decision_card_id=%s", (card,)
                )
        finally:
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
