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


def _section(source: str, name: str) -> str:
    start = source.index(f"{name}()")
    end = source.index("\n}\n", start)
    return source[start:end + 2]


def _gate_sql() -> str:
    contract = _section(SCRIPT.read_text(encoding="utf-8"), "verify_decision_profile_binding_contract")
    marker = 'failed="$(research_query "$database" "'
    return contract[contract.index(marker) + len(marker):contract.index('\n  ")"', contract.index(marker))]


def _restore_state_sql() -> str:
    source = SCRIPT.read_text(encoding="utf-8")
    marker = 'project_state="$(research_query "$RESTORE_DATABASE" "'
    return source[source.index(marker) + len(marker):].split('\")"', 1)[0]


def test_release_requires_031_and_restore_inventory() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    contract = _section(source, "verify_decision_profile_binding_contract")
    for marker in (
        "031.binding", "031.card_fk", "031.profile_fk", "031.requirement_body",
        "031.binding_body", "031.deferred_body", "031.deferred_trigger",
        "031.immutable_trigger", "031.no_nonowner_grants", "ON DELETE RESTRICT",
        "tgtype=23", "tgtype=31", "tgtype=21",
    ):
        assert marker in contract
    assert "verify_decision_profile_binding_contract" in _section(source, "cmd_migrate")
    assert "verify_decision_profile_binding_contract" in source[source.index("cmd_verify()") : source.index("cmd_restore_drill()")]
    restore = source[source.index("cmd_restore_drill()") : source.index("case \"$command_name\"")]
    assert "'10|48'|'11|54')" in restore
    assert 'verify_migration_ledger prefix "$RESTORE_DATABASE"' in restore
    assert "031_decision_card_profile_binding.sql" in restore
    assert "archive_decision_binding_count" in restore
    assert "decision_binding_contract=present" in restore
    assert "decision_binding_contract=%s" in restore
    assert subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True).returncode == 0


def test_binding_archive_counter_counts_only_binding_rows_and_fails_closed() -> None:
    counter = _section(SCRIPT.read_text(encoding="utf-8"), "archive_decision_binding_count")
    code = counter.split("python3 -c '\n", 1)[1].rsplit("\n'", 1)[0]
    valid = (
        "COPY public.project_decision_card (id) FROM stdin;\ncard-a\n\\.\n"
        "COPY public.project_decision_card_profile_binding (id) FROM stdin;\nbinding-a\nbinding-b\n\\.\n"
    )
    result = subprocess.run(["python3", "-c", code], input=valid, text=True, capture_output=True)
    assert result.returncode == 0 and result.stdout == "2\n"
    missing = subprocess.run(["python3", "-c", code], input="COPY public.project_decision_card (id) FROM stdin;\n\\.\n", text=True, capture_output=True)
    assert missing.returncode != 0
    duplicate = subprocess.run(["python3", "-c", code], input=valid + "COPY public.project_decision_card_profile_binding (id) FROM stdin;\n\\.\n", text=True, capture_output=True)
    assert duplicate.returncode != 0


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_031_release_gate_rejects_disabled_or_missing_boundary() -> None:
    assert DSN
    namespace = f"binding_release_{uuid4().hex}"
    query = _gate_sql().replace("public.", f"{namespace}.")
    query = query.replace("schemaname='public'", f"schemaname='{namespace}'")
    query = query.replace("nspname='public'", f"nspname='{namespace}'")
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}, public").format(sql.Identifier(namespace)))
            conn.execute((ROOT / "db/schema.sql").read_text(encoding="utf-8"), prepare=False)
            conn.execute("create table schema_migrations(filename text primary key, sha256 text not null)")
            for migration in sorted((ROOT / "db/migrations").glob("0[2-3][0-9]_*.sql")):
                if "021" <= migration.name[:3] <= "031":
                    conn.execute("insert into schema_migrations(filename,sha256) values (%s,'test')", (migration.name,))
            state_query = _restore_state_sql().replace("public.", f"{namespace}.")
            state_query = state_query.replace("nspname='public'", f"nspname='{namespace}'")
            assert conn.execute(state_query).fetchone()[0] == "11|54"
            assert conn.execute(query).fetchone()[0] == ""
            conn.execute("begin")
            conn.execute("alter table project_decision_card disable trigger trg_project_decision_card_adopt_profile_binding")
            assert "031.deferred_trigger" in conn.execute(query).fetchone()[0]
            conn.execute("rollback")
            conn.execute("begin")
            conn.execute("drop trigger trg_project_decision_card_profile_requirement on project_decision_card")
            conn.execute("create trigger trg_project_decision_card_profile_requirement before update on project_decision_card for each row execute function enforce_project_decision_card_profile_requirement()")
            assert "031.requirement_trigger" in conn.execute(query).fetchone()[0]
            conn.execute("rollback")
            conn.execute("begin")
            conn.execute("create or replace function enforce_project_decision_card_profile_binding() returns trigger language plpgsql as $$ begin return new; end; $$")
            assert "031.binding_body" in conn.execute(query).fetchone()[0]
            conn.execute("rollback")
            conn.execute("begin")
            conn.execute("alter table project_decision_card_profile_binding drop constraint project_decision_card_profile_bindin_profile_id_project_id_fkey")
            assert "031.profile_fk" in conn.execute(query).fetchone()[0]
            conn.execute("rollback")
        finally:
            conn.execute("set search_path to public")
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_031_release_gate_accepts_actual_030_to_031_upgrade() -> None:
    assert DSN
    namespace = f"binding_upgrade_gate_{uuid4().hex}"
    query = _gate_sql().replace("public.", f"{namespace}.")
    query = query.replace("schemaname='public'", f"schemaname='{namespace}'")
    query = query.replace("nspname='public'", f"nspname='{namespace}'")
    schema = (ROOT / "db/schema.sql").read_text(encoding="utf-8")
    baseline = schema.split("-- Fresh-volume bootstrap parity with migration 031.", 1)[0]
    migration = (ROOT / "db/migrations/031_decision_card_profile_binding.sql").read_text(encoding="utf-8")
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}, public").format(sql.Identifier(namespace)))
            conn.execute(baseline, prepare=False)
            conn.execute(migration, prepare=False)
            assert conn.execute(query).fetchone()[0] == ""
        finally:
            conn.execute("set search_path to public")
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
