from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/test-server-release.sh"
ARCHIVE_COUNTER = ROOT / "scripts/test-server-archive-counts.py"
MIGRATION = ROOT / "db/migrations/038_project_asr_standing_grant.sql"
DSN = os.getenv("TEST_DATABASE_URL")


def _contract_sql() -> str:
    source = SCRIPT.read_text(encoding="utf-8")
    section = source.split("verify_project_asr_standing_contract() {", 1)[1].split("\n}\n", 1)[0]
    return section.split('failed="$(research_query "$database" "', 1)[1].split('\n  ")"', 1)[0]


def test_038_release_verify_and_restore_hooks_are_required() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    migrate = source.split("cmd_migrate() {", 1)[1].split("\n}", 1)[0]
    verify = source.split("cmd_verify() {", 1)[1].split("\n\n", 1)[0]
    restore = source.split("cmd_restore_drill()", 1)[1]
    for fragment in ("verify_project_asr_standing_contract", "verify_migration_ledger"):
        assert fragment in migrate or fragment in verify
    for fragment in (
        "038_project_asr_standing_grant.sql", "project_asr_standing_038",
        "asr_standing_source_count", "asr_standing_restored_count",
        "asr_standing_contract=legacy_absent", "asr_standing_contract=present",
    ):
        assert fragment in restore
    assert "asr_standing_contract=%s" in source
    assert subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True).returncode == 0


def test_038_archive_counter_requires_exact_grant_copy_section() -> None:
    sample = "COPY public.project_asr_standing_grant (id) FROM stdin;\nrow-a\nrow-b\n\\.\n"
    valid = subprocess.run(
        ["python3", str(ARCHIVE_COUNTER), "project_asr_standing_038"],
        input=sample, text=True, capture_output=True,
    )
    assert valid.returncode == 0 and valid.stdout.strip() == "2"
    missing = subprocess.run(
        ["python3", str(ARCHIVE_COUNTER), "project_asr_standing_038"],
        input="COPY public.other_table (id) FROM stdin;\nx\n\\.\n", text=True, capture_output=True,
    )
    assert missing.returncode != 0


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_038_release_contract_detects_disabled_trigger_bad_function_and_missing_ledger() -> None:
    assert DSN
    namespace = f"asr_standing_release_{uuid4().hex}"
    query = _contract_sql().replace("public.", f"{namespace}.")
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}, public").format(sql.Identifier(namespace)))
            conn.execute((ROOT / "db/schema.sql").read_text(encoding="utf-8"), prepare=False)
            conn.execute("create table schema_migrations(filename text primary key, sha256 text not null, applied_at timestamptz not null default now())")
            conn.execute(
                "insert into schema_migrations(filename,sha256) values (%s,%s)",
                (MIGRATION.name, hashlib.sha256(MIGRATION.read_bytes()).hexdigest()),
            )
            assert conn.execute(query).fetchone()[0] == ""

            conn.execute("begin")
            conn.execute("alter table project_asr_standing_grant disable trigger trg_project_asr_standing_grant_lifecycle")
            assert "038.trigger.trg_project_asr_standing_grant_lifecycle" in conn.execute(query).fetchone()[0]
            conn.execute("rollback")

            conn.execute("begin")
            conn.execute("""create or replace function enforce_project_asr_standing_grant_lifecycle()
                returns trigger language plpgsql as $$ begin return new; end; $$""")
            assert "038.trigger.trg_project_asr_standing_grant_lifecycle" in conn.execute(query).fetchone()[0]
            conn.execute("rollback")

            conn.execute("begin")
            conn.execute("delete from schema_migrations where filename=%s", (MIGRATION.name,))
            assert "038.ledger" in conn.execute(query).fetchone()[0]
            conn.execute("rollback")
        finally:
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
