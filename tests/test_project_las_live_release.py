from __future__ import annotations

import os
import subprocess
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "scripts/test-server-release.sh"
ARCHIVE_COUNTER = ROOT / "scripts/test-server-archive-counts.py"
DSN = os.getenv("TEST_DATABASE_URL")


def _contract_sql() -> str:
    source = RELEASE.read_text(encoding="utf-8")
    section = source.split("verify_project_las_live_contract() {", 1)[1].split("\n}\n", 1)[0]
    return section.split('failed="$(research_query "$database" "', 1)[1].split('\n  ")"', 1)[0]


def _version_contract_sql() -> str:
    source = RELEASE.read_text(encoding="utf-8")
    section = source.split("verify_project_las_version_contract() {", 1)[1].split("\n}\n", 1)[0]
    return section.split('failed="$(research_query "$database" "', 1)[1].split('\n  ")"', 1)[0]


def _machine_contract_sql() -> str:
    source = RELEASE.read_text(encoding="utf-8")
    section = source.split("verify_project_las_machine_contract() {", 1)[1].split("\n}\n", 1)[0]
    return section.split('failed="$(research_query "$database" "', 1)[1].split('\n  ")"', 1)[0]


def test_041_release_requires_schema_and_restore_inventory() -> None:
    source = RELEASE.read_text(encoding="utf-8")
    assert "verify_project_las_live_contract" in source.split("cmd_migrate() {", 1)[1].split("\n}\n", 1)[0]
    assert "verify_project_las_live_contract" in source.split("cmd_verify() {", 1)[1].split("\ncmd_restore_drill()", 1)[0]
    restore = source.split("cmd_restore_drill()", 1)[1]
    for marker in (
        "041_project_las_live_review.sql", "project_las_live_041",
        "las_live_contract=legacy_absent", "las_live_contract=present",
        "las_live_contract=%s", "las_live_source_counts", "las_live_restored_counts",
    ):
        assert marker in restore
    assert subprocess.run(["bash", "-n", str(RELEASE)], capture_output=True).returncode == 0


def test_041_archive_counter_requires_both_new_table_sections() -> None:
    sample = (
        "COPY public.project_las_video_review (id) FROM stdin;\nreview\n\\.\n"
        "COPY public.project_las_analysis_result (id) FROM stdin;\nresult\n\\.\n"
    )
    valid = subprocess.run(
        ["python3", str(ARCHIVE_COUNTER), "project_las_live_041"],
        input=sample, text=True, capture_output=True,
    )
    assert valid.returncode == 0 and valid.stdout.strip() == "1|1"
    missing = subprocess.run(
        ["python3", str(ARCHIVE_COUNTER), "project_las_live_041"],
        input="COPY public.project_las_video_review (id) FROM stdin;\nreview\n\\.\n",
        text=True, capture_output=True,
    )
    assert missing.returncode != 0


def test_042_release_gate_and_restore_detection_are_wired() -> None:
    source = RELEASE.read_text(encoding="utf-8")
    assert "verify_project_las_version_contract" in source.split("cmd_migrate() {", 1)[1].split("\n}\n", 1)[0]
    assert "verify_project_las_version_contract" in source.split("cmd_verify() {", 1)[1].split("\ncmd_restore_drill()", 1)[0]
    restore = source.split("cmd_restore_drill()", 1)[1]
    assert "042_project_las_version_binding.sql" in restore
    assert 'verify_project_las_version_contract "$RESTORE_DATABASE"' in restore
    assert "las_version_contract=legacy_absent" in restore
    assert "las_version_contract=present" in restore
    assert "las_version_contract=%s" in restore
    assert subprocess.run(["bash", "-n", str(RELEASE)], capture_output=True).returncode == 0


def test_043_release_gate_and_restore_inventory_are_wired() -> None:
    source = RELEASE.read_text(encoding="utf-8")
    assert "verify_project_las_machine_contract" in source.split("cmd_migrate() {", 1)[1].split("\n}\n", 1)[0]
    assert "verify_project_las_machine_contract" in source.split("cmd_verify() {", 1)[1].split("\ncmd_restore_drill()", 1)[0]
    restore = source.split("cmd_restore_drill()", 1)[1]
    for marker in (
        "043_project_las_machine_authorization.sql", "project_las_machine_043",
        "las_machine_contract=legacy_absent", "las_machine_contract=present",
        "las_machine_contract=%s", "las_machine_source_count", "las_machine_restored_count",
    ):
        assert marker in restore
    sample = "COPY public.project_las_video_authorization (id) FROM stdin;\nauth\n\\.\n"
    valid = subprocess.run(
        ["python3", str(ARCHIVE_COUNTER), "project_las_machine_043"],
        input=sample, text=True, capture_output=True,
    )
    assert valid.returncode == 0 and valid.stdout.strip() == "1"
    assert subprocess.run(["bash", "-n", str(RELEASE)], capture_output=True).returncode == 0


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_043_release_gate_detects_ledger_trigger_and_constraint_drift() -> None:
    assert DSN
    namespace = f"las_machine_release_{uuid4().hex}"
    query = _machine_contract_sql().replace("public.", f"{namespace}.")
    query = query.replace("schemaname='public'", f"schemaname='{namespace}'")
    query = query.replace("nspname='public'", f"nspname='{namespace}'")
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            conn.execute((ROOT / "db/schema.sql").read_text(encoding="utf-8"), prepare=False)
            conn.execute("create table schema_migrations(filename text primary key)")
            assert "043.ledger" in conn.execute(query).fetchone()[0]
            conn.execute("insert into schema_migrations(filename) values('043_project_las_machine_authorization.sql')")
            assert conn.execute(query).fetchone()[0] == ""
            conn.execute("begin")
            conn.execute("alter table project_las_video_authorization disable trigger trg_project_las_video_authorization")
            assert "043.trigger.trg_project_las_video_authorization" in conn.execute(query).fetchone()[0]
            conn.execute("rollback")
            conn.execute("begin")
            conn.execute("alter table project_las_analysis_attempt drop constraint project_las_one_permission_source")
            assert "043.one_permission_check" in conn.execute(query).fetchone()[0]
            conn.execute("rollback")
        finally:
            conn.execute("set search_path to public")
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_041_release_gate_detects_missing_ledger_and_trigger_drift() -> None:
    assert DSN
    namespace = f"las_live_release_{uuid4().hex}"
    query = _contract_sql().replace("public.", f"{namespace}.")
    query = query.replace("schemaname='public'", f"schemaname='{namespace}'")
    query = query.replace("nspname='public'", f"nspname='{namespace}'")
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            conn.execute((ROOT / "db/schema.sql").read_text(encoding="utf-8"), prepare=False)
            conn.execute("create table schema_migrations(filename text primary key)")
            assert "041.ledger" in conn.execute(query).fetchone()[0]
            conn.execute("insert into schema_migrations(filename) values('041_project_las_live_review.sql')")
            assert conn.execute(query).fetchone()[0] == ""
            conn.execute("begin")
            conn.execute("alter table project_las_analysis_attempt disable trigger trg_project_las_request_actor")
            assert "041.trigger.trg_project_las_request_actor" in conn.execute(query).fetchone()[0]
            conn.execute("rollback")
            conn.execute("begin")
            conn.execute("drop trigger trg_project_las_request_actor on project_las_analysis_attempt")
            conn.execute(
                "create trigger trg_project_las_request_actor before insert or update "
                "on project_las_analysis_attempt for each row when (false) "
                "execute function enforce_project_las_request_actor()"
            )
            assert "041.trigger.trg_project_las_request_actor" in conn.execute(query).fetchone()[0]
            conn.execute("rollback")
            for function_name, trigger_name in (
                ("enforce_project_las_live_review_binding", "trg_project_las_live_review_binding"),
                ("enforce_project_las_request_actor", "trg_project_las_request_actor"),
            ):
                conn.execute("begin")
                conn.execute(
                    sql.SQL("create or replace function {}() returns trigger language plpgsql "
                            "as $$begin return new; end;$$").format(sql.Identifier(function_name))
                )
                assert f"041.trigger.{trigger_name}" in conn.execute(query).fetchone()[0]
                conn.execute("rollback")
            conn.execute("begin")
            conn.execute("drop trigger trg_project_las_request_actor on project_las_analysis_attempt")
            conn.execute(
                "create trigger trg_project_las_request_actor before insert "
                "on project_las_analysis_attempt for each row "
                "execute function enforce_project_las_request_actor()"
            )
            assert "041.trigger.trg_project_las_request_actor" in conn.execute(query).fetchone()[0]
            conn.execute("rollback")
        finally:
            conn.execute("set search_path to public")
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_042_release_gate_detects_missing_ledger_and_trigger_drift() -> None:
    assert DSN
    namespace = f"las_version_release_{uuid4().hex}"
    query = _version_contract_sql().replace("public.", f"{namespace}.")
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            conn.execute((ROOT / "db/schema.sql").read_text(encoding="utf-8"), prepare=False)
            conn.execute("create table schema_migrations(filename text primary key)")
            assert "042.ledger" in conn.execute(query).fetchone()[0]
            conn.execute("insert into schema_migrations(filename) values('042_project_las_version_binding.sql')")
            assert conn.execute(query).fetchone()[0] == ""
            conn.execute("begin")
            conn.execute("alter table project_las_analysis_attempt disable trigger trg_project_las_attempt_version_binding")
            assert "042.trigger.trg_project_las_attempt_version_binding" in conn.execute(query).fetchone()[0]
            conn.execute("rollback")
            conn.execute("begin")
            conn.execute(
                "create or replace function enforce_project_las_attempt_version_binding() "
                "returns trigger language plpgsql as $$begin return new; end;$$"
            )
            assert "042.trigger.trg_project_las_attempt_version_binding" in conn.execute(query).fetchone()[0]
            conn.execute("rollback")
            conn.execute("begin")
            conn.execute("drop trigger trg_project_las_receipt_version_binding "
                         "on project_las_analysis_receipt")
            conn.execute("create trigger trg_project_las_receipt_version_binding before update "
                         "on project_las_analysis_receipt for each row "
                         "execute function enforce_project_las_receipt_version_binding()")
            assert "042.trigger.trg_project_las_receipt_version_binding" in conn.execute(query).fetchone()[0]
            conn.execute("rollback")
            conn.execute("begin")
            conn.execute("alter table project_las_video_review drop constraint project_las_review_binding_scheme_valid")
            assert "042.scheme_check" in conn.execute(query).fetchone()[0]
            conn.execute("rollback")
            conn.execute("begin")
            conn.execute("alter table project_las_video_review drop constraint project_las_review_binding_scheme_valid")
            conn.execute("alter table project_las_video_review add constraint "
                         "project_las_review_binding_scheme_valid "
                         "check (binding_scheme in ('legacy_key_v1','versioned_bytes_v2'))")
            assert "042.scheme_check" in conn.execute(query).fetchone()[0]
            conn.execute("rollback")
        finally:
            conn.execute("set search_path to public")
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
