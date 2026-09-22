#!/usr/bin/env python3
"""Read-only business-chain audit for the fixed test-server restore database.

This program neither creates nor removes a database.  It deliberately accepts
only the temporary ``test_server_research_restore`` database used by the
test-server restore drill.  Its result is evidence about restored rows, not a
claim that the whole V1 release has been accepted.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from typing import Any


DSN_ENV = "TEST_SERVER_RESTORED_RESEARCH_DSN"
RESTORE_DATABASE = "test_server_research_restore"
BASE_REQUIRED_TABLES = frozenset(
    {
        "source_video",
        "media_asset",
        "transcript",
        "research_task_cost",
        "analysis_run",
        "asr_execution_job",
        "l3_execution_job",
        "asr_media_review",
    }
)
RESEARCH_BRIEF_TABLES = frozenset({"research_brief", "research_brief_run"})
REQUIRED_TABLES = BASE_REQUIRED_TABLES | RESEARCH_BRIEF_TABLES


class AuditError(ValueError):
    """A deliberately secret-free audit failure."""


def _database_name(dsn: str) -> str | None:
    """Parse a PostgreSQL DSN without ever echoing it back to a caller."""
    try:
        from psycopg.conninfo import conninfo_to_dict

        value = conninfo_to_dict(dsn).get("dbname")
    except Exception as exc:  # psycopg intentionally owns the DSN grammar.
        raise AuditError("restored audit DSN is invalid") from exc
    return value if isinstance(value, str) else None


def validate_restore_dsn(dsn: str) -> None:
    if not isinstance(dsn, str) or not dsn.strip() or _database_name(dsn) != RESTORE_DATABASE:
        raise AuditError("restored audit accepts only the fixed temporary restore database")


def _default_connect(dsn: str, *, autocommit: bool = False) -> Any:
    from psycopg import connect

    return connect(dsn, autocommit=autocommit)


def _scalar(cursor: Any, sql: str) -> int:
    cursor.execute(sql)
    row = cursor.fetchone()
    if not isinstance(row, tuple) or len(row) != 1 or isinstance(row[0], bool) or not isinstance(row[0], int):
        raise AuditError("restored audit received an invalid aggregate result")
    if row[0] < 0:
        raise AuditError("restored audit received an invalid aggregate result")
    return row[0]


COUNT_SQL = {
    "source_videos": "select count(*) from source_video",
    "media_assets": "select count(*) from media_asset",
    "transcripts": "select count(*) from transcript",
    "task_costs": "select count(*) from research_task_cost",
    "analysis_runs": "select count(*) from analysis_run",
    "asr_execution_jobs": "select count(*) from asr_execution_job",
    "l3_execution_jobs": "select count(*) from l3_execution_job",
    "research_briefs": "select count(*) from research_brief",
    "research_brief_runs": "select count(*) from research_brief_run",
    "transcripts_with_task_cost": "select count(*) from transcript where task_cost_id is not null",
    "analysis_runs_with_task_cost": "select count(*) from analysis_run where task_cost_id is not null",
}

BAD_LINK_SQL = {
    "media_source": """
        select count(*) from media_asset m
        left join source_video v on v.id=m.video_id
        where v.id is null
    """,
    "transcript_cost": """
        select count(*) from transcript t
        left join research_task_cost c on c.id=t.task_cost_id
        where t.task_cost_id is not null
          and (c.id is null or c.video_id is distinct from t.video_id)
    """,
    "analysis_cost": """
        select count(*) from analysis_run a
        left join research_task_cost c on c.id=a.task_cost_id
        where a.task_cost_id is not null
          and (c.id is null or c.video_id is distinct from a.video_id)
    """,
    "asr_execution_cost": """
        select count(*) from asr_execution_job j
        left join research_task_cost c on c.id=j.task_cost_id
        where (j.task_cost_id is not null and (c.id is null or c.video_id is distinct from j.video_id))
           or (j.status='completed' and (
             j.task_cost_id is null
             or c.status is distinct from 'completed'
             or c.task_key is distinct from j.task_key
             or c.task_type is distinct from 'asr_transcription'
             or c.input_fingerprint is distinct from j.source_fingerprint
           ))
    """,
    "l3_execution_cost": """
        select count(*) from l3_execution_job j
        left join research_task_cost c on c.id=j.task_cost_id
        where (j.task_cost_id is not null and (c.id is null or c.video_id is distinct from j.video_id))
           or (j.status='completed' and (
             j.task_cost_id is null
             or c.status is distinct from 'completed'
             or c.task_key is distinct from j.task_key
             or c.task_type is distinct from 'l3_structured_research'
             or c.input_fingerprint is distinct from j.input_fingerprint
           ))
    """,
    "asr_execution_transcript": """
        select count(*) from asr_execution_job j
        where j.status='completed' and not exists (
          select 1 from transcript t
          where t.video_id=j.video_id and t.task_cost_id=j.task_cost_id
            and t.source_fingerprint is not distinct from j.source_fingerprint
        )
    """,
    "l3_execution_analysis": """
        select count(*) from l3_execution_job j
        where j.status='completed' and not exists (
          select 1 from analysis_run a
          where a.video_id=j.video_id and a.task_cost_id=j.task_cost_id
            and a.status='completed'
            and a.analysis_type='l3_structured_research'
            and a.analysis_level='L3'
            and a.input_fingerprint is not distinct from j.input_fingerprint
        )
    """,
    "asr_execution_media_review": """
        select count(*) from asr_execution_job j
        where j.status='completed' and not exists (
          select 1
          from media_asset a
          join asr_media_review r on r.asset_id=a.id
          where a.video_id=j.video_id
            and a.kind='audio'
            and a.content_sha256 is not distinct from j.source_fingerprint
            and r.active
            and r.asset_fingerprint is not distinct from j.media_ref_fingerprint
            and r.identity_source='windmill_end_user_email_allowlist_v1'
        )
    """,
}

FULL_CHAIN_SQL = """
    select count(distinct v.id)
    from source_video v
    join media_asset m
      on m.video_id=v.id
     and m.kind='audio'
    join transcript t on t.video_id=v.id
    join research_task_cost asr_cost
      on asr_cost.id=t.task_cost_id
     and asr_cost.video_id=v.id
     and asr_cost.status='completed'
     and asr_cost.task_type='asr_transcription'
     and asr_cost.input_fingerprint is not distinct from t.source_fingerprint
     and asr_cost.input_fingerprint is not distinct from m.content_sha256
    join asr_media_review mr
      on mr.asset_id=m.id
     and mr.active
     and mr.identity_source='windmill_end_user_email_allowlist_v1'
    join asr_execution_job aj
      on aj.video_id=v.id
     and aj.task_cost_id=t.task_cost_id
     and aj.status='completed'
     and aj.task_key=asr_cost.task_key
     and aj.source_fingerprint is not distinct from asr_cost.input_fingerprint
     and aj.media_ref_fingerprint is not distinct from mr.asset_fingerprint
    join analysis_run a
      on a.video_id=v.id
     and a.status='completed'
     and a.analysis_type='l3_structured_research'
     and a.analysis_level='L3'
    join research_task_cost l3_cost
      on l3_cost.id=a.task_cost_id
     and l3_cost.video_id=v.id
     and l3_cost.status='completed'
     and l3_cost.task_type='l3_structured_research'
     and l3_cost.input_fingerprint is not distinct from a.input_fingerprint
    join l3_execution_job lj
      on lj.video_id=v.id
     and lj.task_cost_id=a.task_cost_id
     and lj.status='completed'
     and lj.task_key=l3_cost.task_key
     and lj.input_fingerprint is not distinct from l3_cost.input_fingerprint
     and lj.input_fingerprint is not distinct from a.input_fingerprint
"""


def audit(dsn: str, *, connect: Callable[..., Any] = _default_connect) -> dict[str, Any]:
    """Audit one existing restore DB with a single read-only transaction."""
    validate_restore_dsn(dsn)
    # psycopg starts an implicit transaction on a first statement when
    # autocommit is false.  Start our one explicit read-only transaction
    # instead, so the audit cannot accidentally issue a nested BEGIN.
    connection = connect(dsn, autocommit=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute("begin transaction read only")
            cursor.execute("show transaction_read_only")
            row = cursor.fetchone()
            if row != ("on",):
                raise AuditError("restored audit could not establish a read-only transaction")

            cursor.execute("select current_database()")
            if cursor.fetchone() != (RESTORE_DATABASE,):
                raise AuditError("restored audit is not connected to the fixed temporary restore database")
            cursor.execute("set local search_path=public")

            cursor.execute(
                "select table_name from information_schema.tables "
                "where table_schema='public' and table_name=any(%s)",
                (sorted(REQUIRED_TABLES),),
            )
            present = {row[0] for row in cursor.fetchall() if isinstance(row, tuple) and len(row) == 1}
            if not BASE_REQUIRED_TABLES.issubset(present):
                raise AuditError("restored audit is missing one or more required business tables")
            brief_present = present & RESEARCH_BRIEF_TABLES
            if brief_present and brief_present != RESEARCH_BRIEF_TABLES:
                raise AuditError("restored audit has an incomplete research-brief schema")

            cursor.execute(
                "select exists(select 1 from schema_migrations "
                "where filename='020_research_brief.sql')"
            )
            ledger_row = cursor.fetchone()
            if ledger_row not in ((False,), (True,)) or ledger_row[0] != bool(brief_present):
                raise AuditError("restored research-brief schema and migration ledger are inconsistent")
            brief_contract = "present" if brief_present else "legacy_absent"

            count_sql = COUNT_SQL if brief_present else {
                name: sql for name, sql in COUNT_SQL.items()
                if name not in {"research_briefs", "research_brief_runs"}
            }
            counts = {name: _scalar(cursor, sql) for name, sql in count_sql.items()}
            bad_links = {name: _scalar(cursor, sql) for name, sql in BAD_LINK_SQL.items()}
            full_chain_videos = _scalar(cursor, FULL_CHAIN_SQL)
    finally:
        connection.close()

    invalid_link_count = sum(bad_links.values())
    if invalid_link_count:
        status = "invalid_associations"
    elif full_chain_videos == 0:
        status = "insufficient_samples"
    else:
        status = "business_chain_present"
    return {
        "status": status,
        "counts": {**counts, "full_chain_videos": full_chain_videos},
        "invalid_link_count": invalid_link_count,
        "research_brief_contract": brief_contract,
        "v1_release_accepted": False,
    }


def main() -> int:
    dsn = os.environ.get(DSN_ENV)
    if not dsn:
        print("ERROR: restored audit DSN is not configured", file=sys.stderr)
        return 2
    try:
        result = audit(dsn)
    except AuditError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception:
        # Driver exceptions can include endpoints, users, or other sensitive data.
        print("ERROR: restored business audit could not complete", file=sys.stderr)
        return 1

    print("RESTORED_BUSINESS_AUDIT " + json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] == "business_chain_present" else 1


if __name__ == "__main__":
    raise SystemExit(main())
