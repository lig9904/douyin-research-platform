from __future__ import annotations

import os
import subprocess
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/test-server-release.sh"
DSN = os.getenv("TEST_DATABASE_URL")


def _contract_sql() -> str:
    source = SCRIPT.read_text(encoding="utf-8")
    section = source.split("verify_project_las_receipt_contract() {", 1)[1].split("\n}\n", 1)[0]
    return section.split('failed="$(research_query "$database" "', 1)[1].split('\n  ")"', 1)[0]


def test_040_release_requires_schema_and_restore_inventory() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "verify_project_las_receipt_contract" in source.split("cmd_migrate() {", 1)[1].split("\n}\n", 1)[0]
    assert "verify_project_las_receipt_contract" in source.split("cmd_verify() {", 1)[1].split("\ncmd_restore_drill()", 1)[0]
    restore = source.split("cmd_restore_drill()", 1)[1]
    for marker in ("040_project_las_analysis_receipt.sql", "project_las_receipt_040",
                   "las_receipt_contract=present", "las_receipt_contract=%s"):
        assert marker in restore
    assert subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True).returncode == 0


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_040_release_gate_detects_missing_ledger_and_trigger_drift() -> None:
    assert DSN
    namespace = f"las_release_{uuid4().hex}"
    query = _contract_sql().replace("public.", f"{namespace}.")
    query = query.replace("schemaname='public'", f"schemaname='{namespace}'")
    query = query.replace("nspname='public'", f"nspname='{namespace}'")
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            conn.execute((ROOT / "db/schema.sql").read_text(encoding="utf-8"), prepare=False)
            conn.execute("create table schema_migrations(filename text primary key)")
            assert "040.ledger" in conn.execute(query).fetchone()[0]
            conn.execute("insert into schema_migrations(filename) values('040_project_las_analysis_receipt.sql')")
            assert conn.execute(query).fetchone()[0] == ""
            conn.execute("begin")
            conn.execute("alter table project_las_analysis_receipt disable trigger trg_project_las_receipt_asset")
            assert "040.trigger.trg_project_las_receipt_asset" in conn.execute(query).fetchone()[0]
            conn.execute("rollback")
            conn.execute("begin")
            conn.execute("drop trigger trg_project_las_attempt_lifecycle on project_las_analysis_attempt")
            conn.execute(
                "create trigger trg_project_las_attempt_lifecycle before insert or update "
                "on project_las_analysis_attempt for each row when (false) "
                "execute function enforce_project_las_attempt_lifecycle()"
            )
            assert "040.trigger.trg_project_las_attempt_lifecycle" in conn.execute(query).fetchone()[0]
            conn.execute("rollback")
            conn.execute("begin")
            conn.execute(
                "create or replace function enforce_project_las_attempt_lifecycle() "
                "returns trigger language plpgsql as $$begin return new; end;$$"
            )
            assert "040.trigger.trg_project_las_attempt_lifecycle" in conn.execute(query).fetchone()[0]
            conn.execute("rollback")
            conn.execute("begin")
            conn.execute("drop trigger trg_project_las_receipt_asset on project_las_analysis_receipt")
            conn.execute(
                "create trigger trg_project_las_receipt_asset before update "
                "on project_las_analysis_receipt for each row "
                "execute function enforce_project_las_receipt_asset()"
            )
            assert "040.trigger.trg_project_las_receipt_asset" in conn.execute(query).fetchone()[0]
            conn.execute("rollback")
        finally:
            conn.execute("set search_path to public")
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
