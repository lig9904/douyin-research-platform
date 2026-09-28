from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from douyin_research.l0l1 import scoring as scoring_module
from douyin_research.l0l1.scoring import L1Scorer


ROOT = Path(__file__).parents[1]
SCHEMA = ROOT / "db/schema.sql"
MIGRATION = ROOT / "db/migrations/029_project_subject_score.sql"
DSN = os.getenv("TEST_DATABASE_URL")


def test_project_subject_score_schema_has_own_scope_and_no_global_write() -> None:
    migration = MIGRATION.read_text(encoding="utf-8")
    schema = SCHEMA.read_text(encoding="utf-8")
    for source in (migration, schema):
        assert "project_video_subject_score" in source
        assert "references project_video_inclusion(project_id, video_id)" in source
        assert "references research_subject(id, project_id)" in source
        assert "references pipeline_run(id, project_id)" in source
        assert "enforce_project_video_subject_score_eligible" in source
        assert "reject_project_video_subject_score_change" in source
    assert "update source_video" not in migration.lower()
    assert "insert into video_score" not in migration.lower()


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_project_subject_score_is_relevance_gated_and_never_mutates_global_l1(monkeypatch) -> None:
    assert DSN
    namespace = f"project_subject_score_{uuid4().hex}"
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            admin.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            admin.execute(SCHEMA.read_text(encoding="utf-8"), prepare=False)
            # Explicitly replay the upgrade migration as a fresh-volume parity
            # check; all operations are idempotent.
            admin.execute(MIGRATION.read_text(encoding="utf-8"), prepare=False)
            org = admin.execute(
                "insert into research_organization(slug,name) values ('score-org','Score Org') returning id"
            ).fetchone()[0]
            project = admin.execute(
                """insert into research_project(organization_id,slug,name,status)
                   values (%s,'score-project','Score Project','active') returning id""", (org,)
            ).fetchone()[0]
            subject = admin.execute(
                """insert into research_subject(project_id,name,subject_type,status)
                   values (%s,'渔岛','destination','active') returning id""", (project,)
            ).fetchone()[0]
            run = admin.execute(
                """insert into pipeline_run(run_type,platform,project_id)
                   values ('l0l1_discovery','douyin',%s) returning id""", (project,)
            ).fetchone()[0]
            video = admin.execute(
                """insert into source_video(platform,platform_video_id,title,availability_status)
                   values ('douyin','99887766554433221','渔岛温泉','available') returning id"""
            ).fetchone()[0]
            admin.execute(
                """insert into project_video_inclusion(project_id,video_id,source_run_id,source_type)
                   values (%s,%s,%s,'pipeline_run')""", (project, video, run)
            )
            admin.execute(
                """insert into project_video_subject_relevance(
                     project_id,video_id,subject_id,run_id,decision,decision_source,rule_version
                   ) values (%s,%s,%s,%s,'relevant','rule','subject-relevance-v1.0.0')""",
                (project, video, subject, run),
            )
            admin.execute(
                """insert into pipeline_run_item(run_id,entity_type,entity_id,stage,outcome)
                   values (%s,'video',%s,'L0','ingested')""", (run, video)
            )
            global_state_before = admin.execute(
                "select research_level, monitoring_status, monitoring_priority, next_due_at from source_video where id=%s",
                (video,),
            ).fetchone()
            scoped_dsn = make_conninfo(DSN, options=f"-c search_path={namespace}")
            scores = L1Scorer(scoped_dsn).score_run(
                run, project_id=project, subject_id=subject,
            )
            assert scores == {video: 0.0}
            assert admin.execute(
                """select count(*) from project_video_subject_score
                   where project_id=%s and video_id=%s and subject_id=%s and source_run_id=%s""",
                (project, video, subject, run),
            ).fetchone()[0] == 1
            assert admin.execute("select count(*) from video_score where video_id=%s", (video,)).fetchone()[0] == 0
            global_state = admin.execute(
                "select research_level, monitoring_status, monitoring_priority, next_due_at from source_video where id=%s",
                (video,),
            ).fetchone()
            assert global_state == global_state_before

            # Replaying the same run after a calculation change must return
            # the immutable persisted score, not claim a new incompatible one.
            monkeypatch.setattr(scoring_module, "_recency", lambda *_args: 1.0)
            assert L1Scorer(scoped_dsn).score_run(
                run, project_id=project, subject_id=subject,
            ) == {video: 0.0}
            replay_metadata = admin.execute(
                """select metadata from pipeline_run_item
                   where run_id=%s and entity_type='video' and entity_id=%s""",
                (run, video),
            ).fetchone()[0]
            assert replay_metadata["score"] == 0.0
            assert admin.execute("select count(*) from project_video_subject_score").fetchone()[0] == 1

            unavailable_run = admin.execute(
                """insert into pipeline_run(run_type,platform,project_id)
                   values ('l0l1_discovery','douyin',%s) returning id""", (project,)
            ).fetchone()[0]
            admin.execute(
                """insert into pipeline_run_item(run_id,entity_type,entity_id,stage,outcome)
                   values (%s,'video',%s,'L0','ingested')""", (unavailable_run, video)
            )
            admin.execute("update source_video set availability_status='unavailable' where id=%s", (video,))
            assert L1Scorer(scoped_dsn).score_run(
                unavailable_run, project_id=project, subject_id=subject,
            ) == {}
            with pytest.raises(psycopg.errors.RaiseException, match="requires active relevant"):
                admin.execute(
                    """insert into project_video_subject_score(
                         project_id,video_id,subject_id,source_run_id,score,rule_version
                       ) values (%s,%s,%s,%s,50,'test')""",
                    (project, video, subject, unavailable_run),
                )
            admin.execute("update source_video set availability_status='available' where id=%s", (video,))

            # Database enforcement covers direct SQL too: a valid project and
            # relevant subject cannot attach a score to a run that did not
            # actually contain this video.
            orphan_run = admin.execute(
                """insert into pipeline_run(run_type,platform,project_id)
                   values ('l0l1_discovery','douyin',%s) returning id""", (project,)
            ).fetchone()[0]
            with pytest.raises(psycopg.errors.RaiseException, match="requires active relevant"):
                admin.execute(
                    """insert into project_video_subject_score(
                         project_id,video_id,subject_id,source_run_id,score,rule_version
                       ) values (%s,%s,%s,%s,50,'test')""",
                    (project, video, subject, orphan_run),
                )

            # The current durable relevance decision is checked while writing.
            # A score rerun after a manual exclusion cannot insert a new row.
            admin.execute(
                """update project_video_subject_relevance
                   set decision='irrelevant', decision_source='manual', reviewed_by='reviewer@example.com', reviewed_at=now()
                   where project_id=%s and video_id=%s and subject_id=%s""",
                (project, video, subject),
            )
            rerun = admin.execute(
                """insert into pipeline_run(run_type,platform,project_id)
                   values ('l0l1_discovery','douyin',%s) returning id""", (project,)
            ).fetchone()[0]
            admin.execute(
                """insert into pipeline_run_item(run_id,entity_type,entity_id,stage,outcome)
                   values (%s,'video',%s,'L0','ingested')""", (rerun, video)
            )
            assert L1Scorer(scoped_dsn).score_run(
                rerun, project_id=project, subject_id=subject,
            ) == {}
            assert admin.execute("select count(*) from project_video_subject_score").fetchone()[0] == 1
        finally:
            admin.execute("set search_path to public")
            admin.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
