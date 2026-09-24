"""Exercise the 028 release contract against a fresh isolated database schema."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]
DSN = os.getenv("TEST_DATABASE_URL")


def _release_sql(function: str) -> str:
    source = (ROOT / "scripts/test-server-release.sh").read_text(encoding="utf-8")
    function_start = source.index(f"{function}() {{")
    marker = 'failed="$(research_query "$database" "'
    start = source.index(marker, function_start) + len(marker)
    end = source.index('\n  ")"', start)
    return source[start:end]


def _restore_state_sql() -> str:
    source = (ROOT / "scripts/test-server-release.sh").read_text(encoding="utf-8")
    marker = 'project_state="$(research_query "$RESTORE_DATABASE" "'
    start = source.index(marker) + len(marker)
    end = source.index('")"\n  case "$project_state"', start)
    return source[start:end]


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_fresh_schema_has_private_028_contract_and_restore_state() -> None:
    assert DSN
    namespace = f"decision_release_{uuid4().hex}"
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            conn.execute((ROOT / "db/schema.sql").read_text(encoding="utf-8"), prepare=False)
            gate = _release_sql("verify_decision_loop_contract").replace("public.", f"{namespace}.")
            gate = gate.replace("schemaname='public'", f"schemaname='{namespace}'")
            gate = gate.replace("nspname='public'", f"nspname='{namespace}'")
            assert conn.execute(gate).fetchone()[0] == ""
            assert conn.execute(
                gate.replace("idx_project_decision_card_project_status", "missing_decision_index")
            ).fetchone()[0] == "028.card_index"
            conn.execute(sql.SQL("grant select on {}.project_decision_card to public").format(sql.Identifier(namespace)))
            assert conn.execute(gate).fetchone()[0] == "028.no_nonowner_grants"
            conn.execute(sql.SQL("revoke select on {}.project_decision_card from public").format(sql.Identifier(namespace)))

            conn.execute("create table schema_migrations(filename text primary key, sha256 text not null)")
            for migration in sorted((ROOT / "db/migrations").glob("0[2][1-8]_*.sql")):
                conn.execute(
                    "insert into schema_migrations(filename, sha256) values (%s, 'test')",
                    (migration.name,),
                )
            # Reconstruct the 028 archive, not today's fresh bootstrap: 029
            # and 030 contribute later tables, indexes, and functions.
            conn.execute("drop table research_subject_profile_version")
            conn.execute("drop function enforce_research_subject_profile_version()")
            conn.execute("drop function reject_research_subject_profile_version_delete()")
            conn.execute("drop table project_video_subject_score")
            conn.execute("drop function enforce_project_video_subject_score_eligible()")
            conn.execute("drop function reject_project_video_subject_score_change()")
            state = _restore_state_sql().replace("public.", f"{namespace}.")
            state = state.replace("nspname='public'", f"nspname='{namespace}'")
            assert conn.execute(state).fetchone()[0] == "8|38"
        finally:
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
