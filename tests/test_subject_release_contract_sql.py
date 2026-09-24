"""Exercise the 027 release-contract query against an isolated schema."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]
DSN = os.getenv("TEST_DATABASE_URL")


def _subject_gate_sql() -> str:
    source = (ROOT / "scripts/test-server-release.sh").read_text(encoding="utf-8")
    function_start = source.index("verify_subject_relevance_contract() {")
    marker = 'failed="$(research_query "$database" "'
    start = source.index(marker, function_start) + len(marker)
    end = source.index('\n  ")"', start)
    return source[start:end]


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_subject_release_gate_requires_027_shape_indexes_and_private_grants() -> None:
    assert DSN
    namespace = f"subject_release_{uuid4().hex}"
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            conn.execute((ROOT / "db/schema.sql").read_text(encoding="utf-8"), prepare=False)
            gate = _subject_gate_sql().replace("public.", f"{namespace}.")
            gate = gate.replace("schemaname='public'", f"schemaname='{namespace}'")
            gate = gate.replace("nspname='public'", f"nspname='{namespace}'")
            assert conn.execute(gate).fetchone()[0] == ""
            missing_index = gate.replace(
                "idx_project_video_subject_relevance_gate", "idx_missing_subject_gate"
            )
            assert conn.execute(missing_index).fetchone()[0] == "027.relevance_index"
            conn.execute(sql.SQL("grant select on {}.research_subject_term to public").format(sql.Identifier(namespace)))
            assert conn.execute(gate).fetchone()[0] == "027.no_nonowner_grants"
        finally:
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
