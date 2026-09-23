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
    collaboration = re.search(r'collab_checks="(.*?)"\n    if', source, re.S)
    assert common and extra and collaboration
    if mode in {"current", "restored", "pre_accepted_share"}:
        checks = collaboration.group(1)
        if mode == "pre_accepted_share":
            checks = checks.replace(
                "8325f74b3627ea04a3a2fd5506ceeb8e5f00fd660923f7f877682151a7e0e1d1",
                "3bf5615f5cbc79e1bd6d9f2865a4b28c84205bd8fc012742bfb9ac366c2cae62",
            )
        return (gate.replace("$collab_checks", checks)
                    .replace("$mode", mode)
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
            assert conn.execute(missing_share_acl).fetchone()[0] == "025.share_acl,026.share_body"
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
            restored_gate = _gate_sql("restored").replace("public.", f"{namespace}.")
            restored_gate = restored_gate.replace("schemaname='public'", f"schemaname='{namespace}'")
            restored_gate = restored_gate.replace("nspname='public'", f"nspname='{namespace}'")
            assert conn.execute(restored_gate).fetchone()[0] == ""
            conn.execute(sql.SQL("revoke execute on function {}.project_shared_video_can_read(uuid,uuid,text,uuid) from public").format(sql.Identifier(namespace)))
            conn.execute((ROOT / "db/migrations/025_project_collaboration.sql").read_text(encoding="utf-8"), prepare=False)
            old_gate = _gate_sql("pre_accepted_share").replace("public.", f"{namespace}.")
            old_gate = old_gate.replace("schemaname='public'", f"schemaname='{namespace}'")
            old_gate = old_gate.replace("nspname='public'", f"nspname='{namespace}'")
            assert conn.execute(old_gate).fetchone()[0] == ""
            assert conn.execute(gate).fetchone()[0] == "026.share_body"
            conn.execute((ROOT / "db/migrations/026_share_only_accepted_video.sql").read_text(encoding="utf-8"), prepare=False)
            assert conn.execute(gate).fetchone()[0] == ""
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
