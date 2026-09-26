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
    section = source.split("verify_project_private_analysis_contract() {", 1)[1].split("\n}\n", 1)[0]
    return section.split('failed="$(research_query "$database" "', 1)[1].split('\n  ")"', 1)[0]


def test_032_release_and_restore_contract_is_required() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "verify_project_private_analysis_contract" in source.split("cmd_migrate() {", 1)[1].split("\n}\n", 1)[0]
    assert "verify_project_private_analysis_contract" in source.split("cmd_verify() {", 1)[1].split("\ncmd_restore_drill()", 1)[0]
    restore = source.split("cmd_restore_drill()", 1)[1]
    for marker in (
        "project_private_analysis_032", "private_analysis_contract=present",
        "private_analysis_contract=%s", "032_project_private_asr_l3.sql",
    ):
        assert marker in restore
    assert subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True).returncode == 0


def test_032_archive_inventory_fails_closed_on_missing_copy() -> None:
    names = (
        "project_asr_media_review", "project_research_task_cost",
        "project_asr_execution_job", "project_transcript",
        "project_l3_privacy_review", "project_l3_execution_job",
        "project_l3_analysis_result",
    )
    data = "".join(f"COPY public.{name} (id) FROM stdin;\nrow\n\\.\n" for name in names)
    command = ["python3", str(ROOT / "scripts/test-server-archive-counts.py"), "project_private_analysis_032"]
    valid = subprocess.run(command, input=data, text=True, capture_output=True)
    assert valid.returncode == 0 and valid.stdout.strip() == "1|1|1|1|1|1|1"
    missing = subprocess.run(command, input=data.split("COPY public.project_l3_analysis_result", 1)[0],
                             text=True, capture_output=True)
    assert missing.returncode != 0


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_032_release_gate_detects_missing_or_disabled_boundary() -> None:
    assert DSN
    namespace = f"private_release_{uuid4().hex}"
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
            conn.execute("alter table project_asr_execution_job disable trigger trg_project_asr_execution_job_approval")
            assert "trigger.trg_project_asr_execution_job_approval" in conn.execute(query).fetchone()[0]
            conn.execute("rollback")
            conn.execute("begin")
            conn.execute("""create or replace function enforce_project_l3_execution_job_approval()
                returns trigger language plpgsql as $$ begin return new; end; $$""")
            assert "trigger.trg_project_l3_execution_job_approval" in conn.execute(query).fetchone()[0]
            conn.execute("rollback")
            conn.execute("begin")
            conn.execute("alter table project_transcript drop constraint project_transcript_execution_job_id_project_id_video_id_fkey")
            assert "fk.project_transcript.project_asr_execution_job" in conn.execute(query).fetchone()[0]
            conn.execute("rollback")
        finally:
            conn.execute("set search_path to public")
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
