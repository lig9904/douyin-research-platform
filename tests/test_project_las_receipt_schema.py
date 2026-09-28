import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/040_project_las_analysis_receipt.sql"
SCHEMA = ROOT / "db/schema.sql"
DSN = os.getenv("TEST_DATABASE_URL")


def test_las_receipt_schema_matches_additive_migration_and_keeps_billing_separate() -> None:
    migration = MIGRATION.read_text(encoding="utf-8")
    schema = SCHEMA.read_text(encoding="utf-8")
    marker = "-- Project-private whole-video analysis receipts"
    next_marker = "-- Project-local whole-video review"
    assert migration.startswith(marker)
    assert schema.split(marker, 1)[1].split(next_marker, 1)[0].rstrip() == migration.split(marker, 1)[1].rstrip()
    for source in (schema, migration):
        assert "project_las_analysis_receipt" in source
        assert "project_las_supplier_bill_item" in source
        assert "foreign key (project_id,video_id)" in source
        assert "foreign key (asset_id,video_id)" in source
        assert "unique (provider,provider_task_ref)" in source
        assert "supplier_evidence_sha256" in source
        assert "reconciliation_status in ('partial','final')" in source
        assert "project_las_analysis_receipt is append-only" in source
        assert "LAS receipt must bind the reviewed video asset SHA-256" in source
        assert "LAS supplier bill must match its project receipt, account, task and currency" in source
    assert "supplier_actual_cost" not in migration


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_040_migration_is_additive_and_repeatable_on_039_schema() -> None:
    assert DSN
    namespace = f"las_migration_{uuid4().hex}"
    marker = "-- Project-private whole-video analysis receipts"
    previous_schema = SCHEMA.read_text(encoding="utf-8").split(marker, 1)[0]
    migration = MIGRATION.read_text(encoding="utf-8")
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            conn.execute(previous_schema, prepare=False)
            conn.execute(migration, prepare=False)
            conn.execute(migration, prepare=False)
            assert conn.execute(
                """select count(*) from pg_tables where schemaname=%s
                     and tablename in ('project_las_analysis_attempt',
                                       'project_las_analysis_receipt',
                                       'project_las_supplier_bill_item')""",
                (namespace,),
            ).fetchone()[0] == 3
        finally:
            conn.execute("set search_path to public")
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
