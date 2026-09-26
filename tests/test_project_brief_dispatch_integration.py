from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from douyin_research.l0l1.research_briefs import _project_exact_video_transport_guard


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


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_exact_video_first_fetch_requires_claimed_active_project_run() -> None:
    assert DSN
    namespace = f"exact_brief_{uuid4().hex}"
    runner = _runner()
    first, second, other = (
        "7521318904971578681", "7653684558261710777", "7689079072312662185",
    )
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            conn.execute(SCHEMA.read_text(encoding="utf-8"), prepare=False)
            organization_id = conn.execute(
                "insert into research_organization(slug, name) values ('exact-org', 'Exact') returning id"
            ).fetchone()[0]
            project_id = conn.execute(
                """insert into research_project(organization_id, slug, name, status)
                   values (%s, 'exact-project', 'Exact', 'active') returning id""",
                (organization_id,),
            ).fetchone()[0]
            subject_id = conn.execute(
                """insert into research_subject(project_id, name, subject_type)
                   values (%s, 'Exact subject', 'topic') returning id""",
                (project_id,),
            ).fetchone()[0]
            scoped_dsn = make_conninfo(DSN, options=f"-c search_path={namespace}")
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    """insert into research_brief(owner_actor, name, platform, source_type,
                         target, time_window_hours, max_items, depth, status, next_due_at)
                       values ('owner@example.com', 'Unscoped', 'douyin', 'video_ids',
                         %s, 0, 2, 'metadata', 'active', now())""",
                    (f"{first},{second}",),
                )
            boundary_ids = ",".join(str(10**24 + index) for index in range(20))
            assert len(boundary_ids) == 519
            conn.execute(
                """insert into research_brief(
                     owner_actor, name, platform, source_type, target, time_window_hours,
                     max_items, depth, project_id, subject_id, subject_gate_status
                   ) values ('owner@example.com', 'Boundary IDs', 'douyin', 'video_ids',
                     %s, 0, 20, 'metadata', %s, %s, 'ready')""",
                (boundary_ids, project_id, subject_id),
            )
            brief_id = conn.execute(
                """insert into research_brief(
                     owner_actor, name, platform, source_type, target, time_window_hours,
                     max_items, depth, status, next_due_at, project_id, subject_id,
                     subject_gate_status
                   ) values ('owner@example.com', 'Exact IDs', 'douyin', 'video_ids',
                     %s, 0, 2, 'metadata', 'active', now(), %s, %s, 'ready') returning id""",
                (f"{first},{second}", project_id, subject_id),
            ).fetchone()[0]
            expected = (first, second)
            with pytest.raises(PermissionError, match="no longer eligible"):
                with _project_exact_video_transport_guard(
                    scoped_dsn, project_id, subject_id, uuid4(), expected, (first,),
                ):
                    pass
            claim = runner._claim(scoped_dsn, brief_id, "worker")
            assert claim is not None
            run_id = claim["brief_run_id"]
            assert conn.execute(
                "select status, next_due_at from research_brief where id=%s", (brief_id,),
            ).fetchone() == ("paused", None)
            with _project_exact_video_transport_guard(
                scoped_dsn, project_id, subject_id, run_id, expected, (first,),
            ):
                pass
            with pytest.raises(PermissionError, match="outside the claimed task"):
                with _project_exact_video_transport_guard(
                    scoped_dsn, project_id, subject_id, run_id, expected, (other,),
                ):
                    pass
            conn.execute("update research_subject set status='archived' where id=%s", (subject_id,))
            with pytest.raises(PermissionError, match="no longer eligible"):
                with _project_exact_video_transport_guard(
                    scoped_dsn, project_id, subject_id, run_id, expected, (first,),
                ):
                    pass
            conn.execute("update research_subject set status='active' where id=%s", (subject_id,))
            conn.execute("update research_brief set config_version=config_version+1 where id=%s", (brief_id,))
            with pytest.raises(PermissionError, match="no longer eligible"):
                with _project_exact_video_transport_guard(
                    scoped_dsn, project_id, subject_id, run_id, expected, (second,),
                ):
                    pass
        finally:
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
