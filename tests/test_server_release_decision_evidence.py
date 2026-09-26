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
    section = source.split("verify_project_decision_evidence_contract() {", 1)[1].split("\n}\n", 1)[0]
    return section.split('failed="$(research_query "$database" "', 1)[1].split('\n  ")"', 1)[0]


def test_035_release_and_restore_contract_is_required() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "verify_project_decision_evidence_contract" in source.split("cmd_migrate() {", 1)[1].split("\n}\n", 1)[0]
    assert "verify_project_decision_evidence_contract" in source.split("cmd_verify() {", 1)[1].split("\ncmd_restore_drill()", 1)[0]
    restore = source.split("cmd_restore_drill()", 1)[1]
    for marker in (
        "project_decision_evidence_035", "evidence_contract=present",
        "evidence_contract=%s", "035_project_decision_evidence.sql",
    ):
        assert marker in restore
    assert subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True).returncode == 0


def test_035_archive_inventory_fails_closed_on_missing_copy() -> None:
    command = ["python3", str(ROOT / "scripts/test-server-archive-counts.py"), "project_decision_evidence_035"]
    data = "COPY public.project_decision_card_evidence_ref (id) FROM stdin;\nref-a\nref-b\n\\.\n"
    valid = subprocess.run(command, input=data, text=True, capture_output=True)
    assert valid.returncode == 0 and valid.stdout.strip() == "2"
    missing = subprocess.run(command, input="", text=True, capture_output=True)
    assert missing.returncode != 0


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_035_release_gate_detects_missing_or_disabled_guard() -> None:
    assert DSN
    namespace = f"decision_evidence_release_{uuid4().hex}"
    query = _contract_sql().replace("public.", f"{namespace}.")
    query = query.replace("schemaname='public'", f"schemaname='{namespace}'")
    query = query.replace("nspname='public'", f"nspname='{namespace}'")
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}, public").format(sql.Identifier(namespace)))
            conn.execute((ROOT / "db/schema.sql").read_text(encoding="utf-8"), prepare=False)
            assert conn.execute(query).fetchone()[0] == ""
            conn.execute("begin")
            conn.execute("alter table project_decision_card_evidence_ref disable trigger trg_project_decision_card_evidence_ref")
            assert "035.guard" in conn.execute(query).fetchone()[0]
            conn.execute("rollback")
            conn.execute("begin")
            conn.execute("create or replace function enforce_project_decision_card_evidence_ref() returns trigger language plpgsql as $$ begin return new; end; $$")
            assert "035.guard_body" in conn.execute(query).fetchone()[0]
            conn.execute("rollback")
        finally:
            conn.execute("set search_path to public")
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
