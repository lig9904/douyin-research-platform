from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]
DSN = os.getenv("TEST_DATABASE_URL")


def test_local_migrate_script_uses_research_owner_and_backup_first() -> None:
    source = (ROOT / "scripts/local-l3-env.sh").read_text(encoding="utf-8")
    migrate = source.split("cmd_migrate() {", 1)[1].split(
        "\n}\n\ncmd_restore_drill", 1
    )[0]

    assert 'LOCAL_RESEARCH_MIGRATE:-}" == "YES"' in migrate
    assert 'PGPASSWORD="$RESEARCH_DB_PASSWORD" exec psql -U "$RESEARCH_DB_USER"' in migrate
    assert 'PGPASSWORD="$RESEARCH_DB_PASSWORD" exec pg_dump -U "$RESEARCH_DB_USER"' in migrate
    assert "tableowner=current_user" in migrate
    assert "has_table_privilege(current_user" in migrate
    assert migrate.index("pg_dump") < migrate.index("for migration_path")


def test_local_restore_drill_is_gated_checksum_bound_and_cleans_fixed_database() -> None:
    source = (ROOT / "scripts/local-l3-env.sh").read_text(encoding="utf-8")
    restore = source.split("cmd_restore_drill() (", 1)[1].split("\n\ncmd_review", 1)[0]

    assert 'LOCAL_RESEARCH_RESTORE_DRILL:-}" == "YES"' in restore
    assert 'restore_db=local_research_restore' in restore
    assert '"$ROOT_DIR"/work/local-db-migrations/*' in restore
    assert "shasum -a 256 -c SHA256SUMS" in restore
    assert "pg_restore" in restore
    assert "source_counts" in restore and "restored_counts" in restore
    assert "tableowner=current_user" in restore
    assert "trap cleanup_restore EXIT" in restore
    assert "cleanup_restore_strict" in restore
    assert "temporary restore database still exists" in restore


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_013_upgrades_pre_013_schema_with_research_user_ownership() -> None:
    assert DSN
    schema = f"migration_013_{uuid4().hex}"
    migration = (ROOT / "db/migrations/013_research_user_actions.sql").read_text(
        encoding="utf-8"
    )
    pre_013 = """
        create table source_video(id uuid primary key default gen_random_uuid());
        create table source_account(id uuid primary key default gen_random_uuid());
        create table external_signal(id uuid primary key default gen_random_uuid());
        create table collection(
          id uuid primary key default gen_random_uuid(),
          name text not null,
          description text,
          created_by text not null,
          created_at timestamptz not null default now()
        );
        create table collection_item(
          id uuid primary key default gen_random_uuid(),
          collection_id uuid not null references collection(id) on delete cascade,
          video_id uuid references source_video(id) on delete cascade,
          signal_id uuid references external_signal(id) on delete cascade,
          note text,
          created_at timestamptz not null default now(),
          constraint collection_item_check check (
            (video_id is not null)::int + (signal_id is not null)::int = 1
          )
        );
    """

    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema)))
            conn.execute(pre_013, prepare=False)
            conn.execute(migration, prepare=False)
            rows = conn.execute(
                """
                select tablename, tableowner,
                  has_table_privilege(current_user, quote_ident(schemaname) || '.' || quote_ident(tablename), 'SELECT') as can_select,
                  has_table_privilege(current_user, quote_ident(schemaname) || '.' || quote_ident(tablename), 'INSERT') as can_insert,
                  has_table_privilege(current_user, quote_ident(schemaname) || '.' || quote_ident(tablename), 'UPDATE') as can_update
                from pg_tables
                where schemaname=%s and tablename in ('saved_research_filter','research_user_action')
                order by tablename
                """,
                (schema,),
            ).fetchall()
            current_user = conn.execute("select current_user").fetchone()[0]
            assert rows == [
                ("research_user_action", current_user, True, True, True),
                ("saved_research_filter", current_user, True, True, True),
            ]
            assert conn.execute(
                """
                select exists(
                  select 1 from information_schema.columns
                  where table_schema=%s and table_name='collection_item' and column_name='account_id'
                )
                """,
                (schema,),
            ).fetchone()[0]
        finally:
            conn.execute("set search_path to public")
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(schema)))
