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


def _restore_project_state_sql() -> str:
    source = (ROOT / "scripts/test-server-release.sh").read_text(encoding="utf-8")
    marker = 'project_state="$(research_query "$RESTORE_DATABASE" "'
    start = source.index(marker) + len(marker)
    end = source.index('")"\n  case "$project_state"', start)
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


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_restore_state_distinguishes_026_prefix_from_027_archive() -> None:
    assert DSN
    namespace = f"subject_restore_state_{uuid4().hex}"
    filenames = (
        "021_project_account_foundation.sql", "022_project_task_ownership.sql",
        "023_project_video_read_acl.sql", "024_project_account_relation_cursor.sql",
        "025_project_collaboration.sql", "026_share_only_accepted_video.sql",
    )
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            conn.execute((ROOT / "db/schema.sql").read_text(encoding="utf-8"), prepare=False)
            state_sql = _restore_project_state_sql().replace("public.", f"{namespace}.")
            state_sql = state_sql.replace("nspname='public'", f"nspname='{namespace}'")
            conn.execute("create table schema_migrations(filename text primary key, sha256 text not null)")
            for filename in filenames:
                conn.execute("insert into schema_migrations(filename,sha256) values (%s,'test')", (filename,))
            # Simulate an archive restored before 027/028: current bootstrap
            # contains both, so remove their objects in reverse dependency
            # order before checking the historical state classifier.
            conn.execute("drop table project_publication_metric_observation cascade")
            conn.execute("drop table project_publication_record cascade")
            conn.execute("drop table project_decision_card_event cascade")
            conn.execute("drop table project_decision_card cascade")
            for function in (
                "enforce_project_decision_card_accepted_source",
                "enforce_project_decision_card_owner_member",
                "reject_reviewed_project_decision_card_change",
                "reject_project_publication_metric_observation_change",
                "enforce_project_decision_card_review_snapshot",
            ):
                conn.execute(sql.SQL("drop function {}()").format(sql.Identifier(function)))
            conn.execute("drop table project_video_subject_relevance_audit")
            conn.execute("drop table project_video_subject_relevance")
            conn.execute("drop table research_subject_term")
            assert conn.execute(state_sql).fetchone()[0] == "6|17"
            conn.execute((ROOT / "db/migrations/027_subject_relevance_gate.sql").read_text(encoding="utf-8"), prepare=False)
            conn.execute("insert into schema_migrations(filename,sha256) values ('027_subject_relevance_gate.sql','test')")
            assert conn.execute(state_sql).fetchone()[0] == "7|24"
        finally:
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
