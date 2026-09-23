"""Run the release gate's actual SQL against an isolated PostgreSQL schema."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]
DSN = os.getenv("TEST_DATABASE_URL")


def _gate_sql() -> str:
    source = (ROOT / "scripts/test-server-release.sh").read_text(encoding="utf-8")
    marker = 'failed="$(research_query "$database" "'
    start = source.index(marker) + len(marker)
    end = source.index('\n  ")"', start)
    return source[start:end]


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_project_release_gate_detects_missing_cursor_index_and_acl_function() -> None:
    assert DSN
    namespace = f"project_release_{uuid4().hex}"
    schema_sql = (ROOT / "db/schema.sql").read_text(encoding="utf-8")
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            conn.execute(schema_sql, prepare=False)
            gate = _gate_sql().replace("public.", f"{namespace}.")
            gate = gate.replace("schemaname='public'", f"schemaname='{namespace}'")
            gate = gate.replace("nspname='public'", f"nspname='{namespace}'")
            assert conn.execute(gate).fetchone()[0] == ""
            missing_index = gate.replace(
                "idx_project_account_relation_verified_cursor", "idx_missing_for_test"
            )
            assert conn.execute(missing_index).fetchone()[0] == "024.cursor_index"
            missing_acl = gate.replace(
                f"to_regprocedure('{namespace}.project_actor_can_read(uuid,text)')",
                f"to_regprocedure('{namespace}.missing_actor_acl(uuid,text)')",
            )
            assert conn.execute(missing_acl).fetchone()[0] == "023.actor_acl,023.actor_body"
            conn.execute(sql.SQL("grant select on {}.project_account_relation to public").format(sql.Identifier(namespace)))
            assert conn.execute(gate).fetchone()[0] == "project.no_public_grants"
            conn.execute(sql.SQL("revoke select on {}.project_account_relation from public").format(sql.Identifier(namespace)))
            conn.execute(sql.SQL("""
                create or replace function {}.project_actor_can_read(p_project_id uuid, p_actor text)
                returns boolean language sql stable security invoker as $$
                  select p_project_id <> '00000000-0000-0000-0000-000000000000'::uuid
                $$
            """).format(sql.Identifier(namespace)))
            assert conn.execute(gate).fetchone()[0] == "023.actor_body"
        finally:
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
