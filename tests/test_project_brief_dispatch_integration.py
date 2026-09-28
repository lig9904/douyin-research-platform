from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "db/schema.sql"
RUNNER = ROOT / "windmill/f/content_research/collectors/run_research_brief.py"
DSN = os.getenv("TEST_DATABASE_URL")


def _runner():
    spec = importlib.util.spec_from_file_location("project_brief_dispatch_test", RUNNER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_claim_requires_active_project_and_persists_scope() -> None:
    assert DSN
    namespace = f"project_dispatch_{uuid4().hex}"
    runner = _runner()
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            conn.execute(SCHEMA.read_text(encoding="utf-8"), prepare=False)
            organization_id = conn.execute(
                "insert into research_organization(slug, name) values ('org-test', 'Test') returning id"
            ).fetchone()[0]
            project_id = conn.execute(
                """insert into research_project(organization_id, slug, name, status)
                   values (%s, 'project-test', 'Test', 'active') returning id""",
                (organization_id,),
            ).fetchone()[0]
            brief_id = conn.execute(
                """insert into research_brief(
                     owner_actor, name, platform, source_type, time_window_hours,
                     max_items, depth, status, next_due_at, project_id
                   ) values ('owner@example.com', 'Project brief', 'douyin', 'low_fan',
                     24, 1, 'metadata', 'active', now(), %s) returning id""",
                (project_id,),
            ).fetchone()[0]
            scoped_dsn = make_conninfo(DSN, options=f"-c search_path={namespace}")
            # Legacy project briefs without a subject are paused before a
            # worker can issue a paid provider request.
            assert runner._claim(scoped_dsn, brief_id, "worker") is None
            assert conn.execute(
                "select status, subject_gate_status from research_brief where id=%s",
                (brief_id,),
            ).fetchone() == ("paused", "subject_required")
            subject_id = conn.execute(
                """insert into research_subject(project_id, name, subject_type)
                   values (%s, 'Project subject', 'topic') returning id""",
                (project_id,),
            ).fetchone()[0]
            conn.execute(
                """update research_brief set subject_id=%s, subject_gate_status='ready',
                     status='active', next_due_at=now() where id=%s""",
                (subject_id, brief_id),
            )
            claim = runner._claim(scoped_dsn, brief_id, "worker")
            assert claim and claim["project_id"] == project_id
            assert claim["config"]["subject_id"] == str(subject_id)
            assert conn.execute(
                "select project_id from research_brief_run where id = %s",
                (claim["brief_run_id"],),
            ).fetchone()[0] == project_id
            conn.execute(
                "update research_brief set depth='comments', status='active', next_due_at=now() where id=%s",
                (brief_id,),
            )
            assert runner._claim(scoped_dsn, brief_id, "worker") is None
            conn.execute("update research_project set status='paused' where id=%s", (project_id,))
            conn.execute(
                "update research_brief set depth='metadata', status='active', next_due_at=now() where id=%s",
                (brief_id,),
            )
            assert runner._claim(scoped_dsn, brief_id, "worker") is None
        finally:
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
