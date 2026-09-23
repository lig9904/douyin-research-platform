from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/022_project_task_ownership.sql"
MIGRATION_020 = ROOT / "db/migrations/020_research_brief.sql"
MIGRATION_021 = ROOT / "db/migrations/021_project_account_foundation.sql"
SCHEMA = ROOT / "db/schema.sql"
DSN = os.getenv("TEST_DATABASE_URL")


def test_project_task_ownership_is_present_without_canonical_backfill() -> None:
    migration = MIGRATION.read_text(encoding="utf-8")
    schema = SCHEMA.read_text(encoding="utf-8")
    for source in (migration, schema):
        assert "add column" in source
        assert "project_id uuid references research_project(id)" in source
        assert "create table" in source and "project_video_inclusion" in source
        assert "primary key (project_id, video_id)" in source
        assert "source_run_id uuid" in source
        assert "brief_run_id uuid" in source
        assert "first_seen_at timestamptz" in source
        assert "last_seen_at timestamptz" in source
        assert "references pipeline_run(id, project_id)" in source
        assert "references research_brief_run(id, project_id)" in source
        assert "is distinct from new.project_id" in source
        assert "trg_research_brief_run_project_scope" in source
        assert "uq_research_brief_owner_name" in source
        assert "uq_research_brief_project_name" in source
        assert "enforce_project_id_immutable" in source
        assert "update source_video" not in source.lower()
        assert "update source_account" not in source.lower()
        assert "insert into project_video_inclusion" not in source.lower()


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_022_enforces_project_scope_and_preserves_legacy_null_rows() -> None:
    assert DSN
    namespace = f"migration_022_{uuid4().hex}"
    migration = MIGRATION.read_text(encoding="utf-8")
    bootstrap = """
        create table research_project (id uuid primary key default gen_random_uuid());
        create table source_video (id uuid primary key default gen_random_uuid());
        create table research_brief (
          id uuid primary key default gen_random_uuid(),
          owner_actor text not null default 'owner-a',
          name text not null default 'brief',
          status text not null default 'draft',
          next_due_at timestamptz
        );
        create table pipeline_run (
          id uuid primary key default gen_random_uuid(),
          started_at timestamptz not null default now()
        );
        create table research_brief_run (
          id uuid primary key default gen_random_uuid(),
          brief_id uuid not null references research_brief(id) on delete cascade,
          source_run_id uuid references pipeline_run(id) on delete set null,
          started_at timestamptz not null default now()
        );
    """
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            conn.execute(bootstrap, prepare=False)
            conn.execute(migration, prepare=False)
            # The release process may bootstrap from db/schema.sql and then
            # replay numbered migrations; retrying the migration itself must
            # preserve every constraint rather than fail or skip it.
            conn.execute(migration, prepare=False)

            project_a = conn.execute("insert into research_project default values returning id").fetchone()[0]
            project_b = conn.execute("insert into research_project default values returning id").fetchone()[0]
            video = conn.execute("insert into source_video default values returning id").fetchone()[0]
            other_video = conn.execute("insert into source_video default values returning id").fetchone()[0]
            brief_a = conn.execute(
                "insert into research_brief(project_id) values (%s) returning id", (project_a,)
            ).fetchone()[0]
            brief_b = conn.execute(
                "insert into research_brief(project_id) values (%s) returning id", (project_b,)
            ).fetchone()[0]
            legacy_brief = conn.execute("insert into research_brief default values returning id").fetchone()[0]
            conn.execute(
                "insert into research_brief(project_id, name) values (%s, 'shared-name')",
                (project_a,),
            )
            conn.execute(
                "insert into research_brief(project_id, name) values (%s, 'shared-name')",
                (project_b,),
            )
            with pytest.raises(psycopg.errors.UniqueViolation):
                conn.execute(
                    "insert into research_brief(project_id, name) values (%s, 'shared-name')",
                    (project_a,),
                )
            run_a = conn.execute(
                "insert into pipeline_run(project_id) values (%s) returning id", (project_a,)
            ).fetchone()[0]
            run_b = conn.execute(
                "insert into pipeline_run(project_id) values (%s) returning id", (project_b,)
            ).fetchone()[0]
            legacy_run = conn.execute("insert into pipeline_run default values returning id").fetchone()[0]

            # Existing records remain valid without invented ownership.
            legacy_brief_run = conn.execute(
                "insert into research_brief_run(brief_id, source_run_id) values (%s, %s) returning id",
                (legacy_brief, legacy_run),
            ).fetchone()[0]
            assert conn.execute(
                "select project_id from research_brief_run where id = %s", (legacy_brief_run,)
            ).fetchone()[0] is None

            brief_run_a = conn.execute(
                """insert into research_brief_run(brief_id, source_run_id, project_id)
                   values (%s, %s, %s) returning id""",
                (brief_a, run_a, project_a),
            ).fetchone()[0]
            with pytest.raises(psycopg.errors.RaiseException, match="must match research_brief"):
                conn.execute(
                    "insert into research_brief_run(brief_id, project_id) values (%s, %s)",
                    (brief_b, project_a),
                )
            with pytest.raises(psycopg.errors.RaiseException, match="must match research_brief"):
                conn.execute("insert into research_brief_run(brief_id) values (%s)", (brief_a,))
            with pytest.raises(psycopg.errors.RaiseException, match="must match source pipeline_run"):
                conn.execute(
                    "insert into research_brief_run(brief_id, source_run_id, project_id) values (%s, %s, %s)",
                    (brief_a, run_b, project_a),
                )
            with pytest.raises(psycopg.errors.RaiseException, match="project_id is immutable"):
                conn.execute("update research_brief set project_id = %s where id = %s", (project_b, brief_a))
            with pytest.raises(psycopg.errors.RaiseException, match="project_id is immutable"):
                conn.execute("update pipeline_run set project_id = %s where id = %s", (project_b, run_a))

            conn.execute(
                """insert into project_video_inclusion(
                     project_id, video_id, source_run_id, source_type
                   ) values (%s, %s, %s, 'pipeline_run')""",
                (project_a, video, run_a),
            )
            with pytest.raises(psycopg.errors.RaiseException, match="project_id is immutable"):
                conn.execute(
                    "update project_video_inclusion set project_id = %s where project_id = %s and video_id = %s",
                    (project_b, project_a, video),
                )
            with pytest.raises(psycopg.errors.UniqueViolation):
                conn.execute(
                    """insert into project_video_inclusion(
                         project_id, video_id, source_run_id, source_type
                       ) values (%s, %s, %s, 'pipeline_run')""",
                    (project_a, video, run_a),
                )
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                conn.execute(
                    """insert into project_video_inclusion(
                         project_id, video_id, source_run_id, source_type
                       ) values (%s, %s, %s, 'pipeline_run')""",
                    (project_a, other_video, run_b),
                )
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                conn.execute(
                    """insert into project_video_inclusion(
                         project_id, video_id, brief_run_id, source_type
                       ) values (%s, %s, %s, 'research_brief_run')""",
                    (project_b, other_video, brief_run_a),
                )
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "insert into project_video_inclusion(project_id, video_id, source_type) values (%s, %s, 'pipeline_run')",
                    (project_b, other_video),
                )
        finally:
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_022_replays_after_full_schema_bootstrap_and_prior_migrations() -> None:
    assert DSN
    namespace = f"schema_then_022_{uuid4().hex}"
    schema = SCHEMA.read_text(encoding="utf-8")
    migration = MIGRATION.read_text(encoding="utf-8")
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            conn.execute(schema, prepare=False)
            conn.execute(MIGRATION_020.read_text(encoding="utf-8"), prepare=False)
            conn.execute(MIGRATION_021.read_text(encoding="utf-8"), prepare=False)
            conn.execute(migration, prepare=False)
            assert conn.execute(
                "select to_regclass('project_video_inclusion') is not null"
            ).fetchone()[0]
            assert conn.execute(
                """select exists(
                     select 1 from pg_trigger
                     where tgrelid = 'research_brief_run'::regclass
                       and tgname = 'trg_research_brief_run_project_scope'
                       and not tgisinternal
                   )"""
            ).fetchone()[0]
        finally:
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
