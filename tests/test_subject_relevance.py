from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from douyin_research.l0l1.scoring import Candidate, L1Scorer
from douyin_research.l0l1.research_briefs import make_config, run_live
from douyin_research.l0l1.runner import L0L1Runner
from douyin_research.l0l1.subject_relevance import SubjectRelevanceStore, SubjectTerms, classify


ROOT = Path(__file__).parents[1]
MIGRATION = ROOT / "db/migrations/027_subject_relevance_gate.sql"
SCHEMA = ROOT / "db/schema.sql"
DSN = os.getenv("TEST_DATABASE_URL")


def _terms(*, geography: tuple[str, ...] = ()) -> SubjectTerms:
    return SubjectTerms(
        subject_name="渔岛",
        aliases=("渔岛", "Yudao"),
        geographic_contexts=geography,
        exclusions=("招聘", "无关"),
    )


def test_rule_classification_requires_positive_subject_and_optional_geography() -> None:
    assert classify("渔岛温泉周末攻略", _terms()).decision == "relevant"
    assert classify("普通温泉周末攻略", _terms()).decision == "pending"
    assert classify("渔岛招聘公告", _terms()).decision == "irrelevant"
    assert classify("Yudao 温泉", _terms(geography=("秦皇岛",))).decision == "pending"
    result = classify("秦皇岛渔岛温泉攻略", _terms(geography=("秦皇岛",)))
    assert result.decision == "relevant"
    assert result.matched_aliases == ("渔岛",)
    assert result.matched_geographic_contexts == ("秦皇岛",)


def test_manual_correction_validation_is_bounded_before_database_access() -> None:
    store = SubjectRelevanceStore("postgresql://unused")
    with pytest.raises(ValueError, match="decision"):
        store.correct(
            project_id=__import__("uuid").uuid4(), subject_id=__import__("uuid").uuid4(),
            video_id=__import__("uuid").uuid4(), actor="reviewer@example.com",
            decision="maybe", reason="明确相关",
        )
    with pytest.raises(ValueError, match="correction"):
        store.correct(
            project_id=__import__("uuid").uuid4(), subject_id=__import__("uuid").uuid4(),
            video_id=__import__("uuid").uuid4(), actor="", decision="relevant", reason="",
        )


def test_project_research_cannot_enter_runner_or_live_brief_without_subject() -> None:
    project = uuid4()
    runner = L0L1Runner(provider=object(), store=object(), scorer=object(), budget=object())
    with pytest.raises(ValueError, match="both project and subject"):
        runner.run([], project_id=project)
    config = make_config(
        source_type="keyword", target="渔岛", time_window_hours=24,
        max_items=1, depth="metadata", cadence_hours=None,
    )
    with pytest.raises(ValueError, match="requires a subject"):
        run_live(dsn="postgresql://unused", api_key="test", config=config,
                 triggered_by="test", project_id=project)


def test_schema_keeps_source_evidence_global_and_relevance_project_scoped() -> None:
    migration = MIGRATION.read_text(encoding="utf-8")
    schema = SCHEMA.read_text(encoding="utf-8")
    for source in (migration, schema):
        assert "research_subject_term" in source
        assert "term_type in ('alias', 'geographic_context', 'exclusion')" in source
        assert "project_video_subject_relevance" in source
        assert "decision in ('pending', 'relevant', 'irrelevant')" in source
        assert "decision_source in ('rule', 'manual')" in source
        assert "project_video_subject_relevance_audit" in source
        assert "run_id uuid" in source
        assert "manual_override" in source
        assert "update source_video" not in source.lower()
        assert "delete from source_video" not in source.lower()
    assert "references project_video_inclusion(project_id, video_id)" in migration
    assert "subject_id uuid" in migration
    assert "referenced_table.relname='project_video_subject_relevance'" in migration
    assert "drop constraint %I" in migration


def test_subject_routes_are_actor_bound_and_ui_closes_project_brief_path() -> None:
    app = ROOT / "windmill/f/content_research/research_dashboard.raw_app"
    reader = (app / "backend/get_project_subjects.py").read_text(encoding="utf-8")
    writer = (app / "backend/mutate_project_subject.py").read_text(encoding="utf-8")
    panel = (app / "SubjectRelevancePanel.tsx").read_text(encoding="utf-8")
    briefs = (app / "ResearchBriefs.tsx").read_text(encoding="utf-8")
    for source in (reader, writer):
        assert 'os.environ.get("WM_END_USER_EMAIL"' in source
        assert "RESEARCH_PROJECT" in source
        assert "httpx" not in source
    assert "correct_relevance" in writer
    assert "manual_override" in writer
    assert "backend.get_project_subjects" in panel
    assert "backend.mutate_project_subject" in panel
    assert "subject_id: values?.subject_id" in briefs
    assert "请选择研究主体" in briefs


def test_gate_rechecks_current_manual_decision_and_subject_activity() -> None:
    relevance = (ROOT / "src/douyin_research/l0l1/subject_relevance.py").read_text(encoding="utf-8")
    scoring = (ROOT / "src/douyin_research/l0l1/scoring.py").read_text(encoding="utf-8")
    brief = (ROOT / "src/douyin_research/l0l1/research_briefs.py").read_text(encoding="utf-8")
    collector = (ROOT / "windmill/f/content_research/collectors/run_research_brief.py").read_text(encoding="utf-8")
    assert "use the durable current decision" in relevance
    assert "select decision from project_video_subject_relevance" in relevance
    assert "for share" in scoring
    assert "relevance.decision='relevant'" in scoring
    assert "s.status='active'" in brief
    assert "subject.status='active'" in collector
    assert "subject_gate_status='subject_required'" in collector
    assert "unchanged conclusion is still an event" in relevance


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_027_pauses_unbound_active_briefs_and_manual_race_uses_durable_decision() -> None:
    """Exercise 027 and the conditional-upsert race with two DB connections."""
    assert DSN
    namespace = f"subject_gate_{uuid4().hex}"
    migration = MIGRATION.read_text(encoding="utf-8")
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            admin.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            admin.execute(SCHEMA.read_text(encoding="utf-8"), prepare=False)
            org = admin.execute(
                "insert into research_organization(slug,name) values ('subject-gate','Subject Gate') returning id"
            ).fetchone()[0]
            project = admin.execute(
                """insert into research_project(organization_id,slug,name,status)
                   values (%s,'subject-project','Subject Project','active') returning id""", (org,)
            ).fetchone()[0]
            subject = admin.execute(
                """insert into research_subject(project_id,name,subject_type)
                   values (%s,'渔岛','destination') returning id""", (project,)
            ).fetchone()[0]
            video = admin.execute(
                """insert into source_video(platform,platform_video_id,title,availability_status)
                   values ('douyin','98765432123456789','渔岛温泉','available') returning id"""
            ).fetchone()[0]
            admin.execute(
                """insert into project_video_inclusion(project_id,video_id,source_type,status)
                   values (%s,%s,'manual','accepted')""", (project, video)
            )
            admin.execute(
                """insert into project_video_subject_relevance(
                     project_id,video_id,subject_id,decision,decision_source,rule_version
                   ) values (%s,%s,%s,'relevant','rule','subject-relevance-v1.0.0')""",
                (project, video, subject),
            )
            brief = admin.execute(
                """insert into research_brief(owner_actor,project_id,name,platform,source_type,target,
                     time_window_hours,max_items,depth,cadence_hours,status,next_due_at)
                   values ('owner@example.com',%s,'旧项目任务','douyin','keyword','渔岛',24,1,'metadata',6,'active',now())
                   returning id""", (project,)
            ).fetchone()[0]
            # Simulate an early 027 installation whose automatically-generated
            # FK name may have been truncated by PostgreSQL.
            admin.execute(
                """alter table project_video_subject_relevance_audit
                   add foreign key (project_id,video_id,subject_id)
                   references project_video_subject_relevance(project_id,video_id,subject_id)
                   on delete cascade"""
            )
            admin.execute(migration, prepare=False)
            legacy_fk = admin.execute(
                """select 1 from pg_constraint c join pg_class target on target.oid=c.confrelid
                   where c.conrelid='project_video_subject_relevance_audit'::regclass
                     and c.contype='f' and target.relname='project_video_subject_relevance'"""
            ).fetchone()
            assert legacy_fk is None
            state = admin.execute(
                "select status,subject_gate_status,next_due_at is null from research_brief where id=%s", (brief,)
            ).fetchone()
            assert state == ("paused", "subject_required", True)

            scoped_dsn = make_conninfo(DSN, options=f"-c search_path={namespace}")
            run_one = admin.execute(
                """insert into pipeline_run(run_type,platform,project_id)
                   values ('subject_rule_one','douyin',%s) returning id""", (project,)
            ).fetchone()[0]
            run_two = admin.execute(
                """insert into pipeline_run(run_type,platform,project_id)
                   values ('subject_rule_two','douyin',%s) returning id""", (project,)
            ).fetchone()[0]
            store = SubjectRelevanceStore(scoped_dsn)
            assert store.evaluate_run(project_id=project, subject_id=subject, run_id=run_one, video_ids=[video])[video] == "relevant"
            assert store.evaluate_run(project_id=project, subject_id=subject, run_id=run_two, video_ids=[video])[video] == "relevant"
            audit_runs = admin.execute(
                """select run_id from project_video_subject_relevance_audit
                   where event_type='rule_evaluated' order by id"""
            ).fetchall()
            assert audit_runs == [(run_one,), (run_two,)]

            scoped = f"-c search_path={namespace}"
            with psycopg.connect(DSN, options=scoped) as rule_conn, psycopg.connect(DSN, options=scoped) as manual_conn:
                # Rule evaluation observed a rule row. A reviewer commits a
                # correction before the conditional rule upsert obtains lock.
                assert rule_conn.execute(
                    "select decision from project_video_subject_relevance where project_id=%s and video_id=%s and subject_id=%s",
                    (project, video, subject),
                ).fetchone()[0] == "relevant"
                manual_conn.execute(
                    """update project_video_subject_relevance set decision='irrelevant', decision_source='manual',
                         reviewed_by='reviewer@example.com', reviewed_at=now() where project_id=%s and video_id=%s and subject_id=%s""",
                    (project, video, subject),
                )
                manual_conn.commit()
                outcome = rule_conn.execute(
                    """insert into project_video_subject_relevance(
                         project_id,video_id,subject_id,decision,decision_source,rule_version
                       ) values (%s,%s,%s,'relevant','rule','subject-relevance-v1.0.0')
                       on conflict (project_id,video_id,subject_id) do update set decision=excluded.decision
                       where project_video_subject_relevance.decision_source='rule'
                       returning decision""",
                    (project, video, subject),
                ).fetchone()
                assert outcome is None
                durable = rule_conn.execute(
                    "select decision from project_video_subject_relevance where project_id=%s and video_id=%s and subject_id=%s",
                    (project, video, subject),
                ).fetchone()[0]
                assert durable == "irrelevant"
            run = admin.execute(
                """insert into pipeline_run(run_type,platform,project_id)
                   values ('subject_test','douyin',%s) returning id""", (project,)
            ).fetchone()[0]
            admin.execute(
                """insert into pipeline_run_item(run_id,entity_type,entity_id,stage,outcome)
                   values (%s,'video',%s,'L0','ingested')""", (run, video)
            )
            scorer = L1Scorer(scoped_dsn)
            # Project runs cannot be routed through the shared video_score
            # writer, including by a direct internal call that omits scope.
            with pytest.raises(ValueError, match="requires project and subject"):
                scorer.score_run(run)
            with pytest.raises(RuntimeError, match="project-specific score storage"):
                scorer.score_run(run, project_id=project, subject_id=subject)
            assert admin.execute("select count(*) from video_score where video_id=%s", (video,)).fetchone()[0] == 0
            # The audit row is historical: removal of the mutable current
            # decision must not cascade away the manual reason or run link.
            store.correct(
                project_id=project, subject_id=subject, video_id=video,
                actor="reviewer@example.com", decision="irrelevant", reason="复核后排除",
            )
            before_delete = admin.execute(
                "select count(*) from project_video_subject_relevance_audit"
            ).fetchone()[0]
            admin.execute(
                """delete from project_video_subject_relevance
                   where project_id=%s and subject_id=%s and video_id=%s""",
                (project, subject, video),
            )
            assert admin.execute(
                "select count(*) from project_video_subject_relevance_audit"
            ).fetchone()[0] == before_delete
        finally:
            admin.execute("set search_path to public")
            admin.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
