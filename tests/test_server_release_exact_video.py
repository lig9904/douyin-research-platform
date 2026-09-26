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
    section = source.split("verify_project_exact_video_brief_contract() {", 1)[1].split("\n}\n", 1)[0]
    return section.split('failed="$(research_query "$database" "', 1)[1].split('\n  ")"', 1)[0]


def test_036_release_requires_exact_video_contract_and_restores_it() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "verify_project_exact_video_brief_contract" in source.split("cmd_migrate() {", 1)[1].split("\n}\n", 1)[0]
    assert "verify_project_exact_video_brief_contract" in source.split("cmd_verify() {", 1)[1].split("\ncmd_restore_drill()", 1)[0]
    restore = source.split("cmd_restore_drill()", 1)[1]
    assert "036_project_exact_video_brief.sql" in restore
    assert "exact_video_contract=present" in restore
    assert subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True).returncode == 0


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_036_release_contract_detects_removed_project_gate() -> None:
    assert DSN
    namespace = f"exact_video_release_{uuid4().hex}"
    query = _contract_sql().replace("public.", f"{namespace}.")
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            conn.execute((ROOT / "db/schema.sql").read_text(encoding="utf-8"), prepare=False)
            assert conn.execute(query).fetchone()[0] == ""
            conn.execute("begin")
            conn.execute("alter table research_brief drop constraint research_brief_exact_project_check")
            assert "036.project" in conn.execute(query).fetchone()[0]
            conn.execute("rollback")
        finally:
            conn.execute("set search_path to public")
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
