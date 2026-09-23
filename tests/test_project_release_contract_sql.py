"""Run the release gate's actual SQL against an isolated PostgreSQL schema."""

from __future__ import annotations

import os
import re
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]
DSN = os.getenv("TEST_DATABASE_URL")


def _gate_sql(mode: str = "current") -> str:
    source = (ROOT / "scripts/test-server-release.sh").read_text(encoding="utf-8")
    marker = 'failed="$(research_query "$database" "'
    start = source.index(marker) + len(marker)
    end = source.index('\n  ")"', start)
    gate = source[start:end]
    common = re.search(r'owner_tables="([^"\n]+)"', source)
    extra = re.search(r'owner_tables="\$owner_tables,([^"\n]+)"', source)
    collaboration = re.search(r'collab_checks="(.*?)"\n  elif', source, re.S)
    assert common and extra and collaboration
    if mode == "current":
        return (gate.replace("$collab_checks", collaboration.group(1))
                    .replace("$owner_count", "12")
                    .replace("$owner_only_tables", common.group(1) + ",'effective_account_authorization'," + extra.group(1))
                    .replace("$owner_tables", common.group(1) + "," + extra.group(1)))
    assert mode == "pre_collaboration"
    return (gate.replace("$collab_checks", "")
                .replace("$owner_count", "10")
                .replace("$owner_only_tables", common.group(1) + ",'effective_account_authorization'")
                .replace("$owner_tables", common.group(1)))


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
            missing_share_index = gate.replace(
                "idx_project_video_share_target_active", "idx_missing_share_for_test"
            )
            assert conn.execute(missing_share_index).fetchone()[0] == "025.share_cursor"
            missing_share_acl = gate.replace(
                f"to_regprocedure('{namespace}.project_shared_video_can_read(uuid,uuid,text,uuid)')",
                f"to_regprocedure('{namespace}.missing_share_acl(uuid,uuid,text,uuid)')",
            )
            assert conn.execute(missing_share_acl).fetchone()[0] == "025.share_acl,025.share_body"
            missing_acl = gate.replace(
                f"to_regprocedure('{namespace}.project_actor_can_read(uuid,text)')",
                f"to_regprocedure('{namespace}.missing_actor_acl(uuid,text)')",
            )
            assert conn.execute(missing_acl).fetchone()[0] == "023.actor_acl,023.actor_body"
            conn.execute(sql.SQL("grant select on {}.project_account_relation to public").format(sql.Identifier(namespace)))
            assert conn.execute(gate).fetchone()[0] == "project.owner_only_grants"
            conn.execute(sql.SQL("revoke select on {}.project_account_relation from public").format(sql.Identifier(namespace)))
            conn.execute(sql.SQL("grant execute on function {}.project_shared_video_can_read(uuid,uuid,text,uuid) to public").format(sql.Identifier(namespace)))
            assert conn.execute(gate).fetchone()[0] == "025.share_public_execute"
            conn.execute(sql.SQL("revoke execute on function {}.project_shared_video_can_read(uuid,uuid,text,uuid) from public").format(sql.Identifier(namespace)))
            legacy_gate = _gate_sql("pre_collaboration").replace("public.", f"{namespace}.")
            legacy_gate = legacy_gate.replace("schemaname='public'", f"schemaname='{namespace}'")
            legacy_gate = legacy_gate.replace("nspname='public'", f"nspname='{namespace}'")
            assert conn.execute(legacy_gate).fetchone()[0] == ""
            conn.execute(sql.SQL("""
                create or replace function {}.project_actor_can_read(p_project_id uuid, p_actor text)
                returns boolean language sql stable security invoker as $$
                  select p_project_id <> '00000000-0000-0000-0000-000000000000'::uuid
                $$
            """).format(sql.Identifier(namespace)))
            assert conn.execute(gate).fetchone()[0] == "023.actor_body"
        finally:
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
