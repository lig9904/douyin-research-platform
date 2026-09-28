# /// script
# requires-python = "==3.14.*"
# dependencies = ["douyin-research-platform @ git+https://github.com/lig9904/douyin-research-platform@5b93ffa7681038ecbd355c5ffbbbb3b5cdb53180", "psycopg[binary]==3.3.6", "wmill==1.815.0"]
# ///
"""Scheduled recovery of explicitly prepared LAS work, never auto-approval.

One run selects at most four human-requested new submissions and eight task-ID
polls. Unknown submissions without a task ID are deliberately not retried.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
import re

import psycopg
from psycopg.conninfo import make_conninfo


_SCOPE = re.compile(r"[A-Za-z0-9._:-]{1,160}\Z")
_DISPATCH_PATH = "f/content_research/project_las/dispatch_video"
_POLL_PATH = "f/content_research/project_las/poll_video"
_SCHEDULE_PATH = "f/content_research/project_las/run_pending"


@contextmanager
def _single_runner(dsn: str, account_scope: str):
    """Prevent overlapping scheduled batches for one supplier account."""
    key = int.from_bytes(
        hashlib.sha256(f"project-las-batch:{account_scope}".encode()).digest()[:8],
        "big", signed=True,
    )
    # A session lock must remain attached to this dedicated connection while
    # Windmill waits for child jobs; autocommit avoids a long SQL transaction.
    with psycopg.connect(dsn, autocommit=True) as conn:
        acquired = conn.execute("select pg_try_advisory_lock(%s)", (key,)).fetchone()[0]
        if not acquired:
            yield False
            return
        try:
            yield True
        finally:
            conn.execute("select pg_advisory_unlock(%s)", (key,))


def _pending(dsn: str, account_scope: str):
    with psycopg.connect(dsn) as conn:
        conn.execute("set transaction read only")
        dispatch = conn.execute(
            """select attempt.id from project_las_analysis_attempt attempt
               left join project_las_video_review review on review.id=attempt.video_review_id
               left join project_las_video_authorization permission_row
                 on permission_row.id=attempt.video_authorization_id
               where attempt.record_mode='live_pre_dispatch'
                 and attempt.provider='volcengine_las'
                 and attempt.account_scope=%s and attempt.status='prepared'
                 and (review.status='approved' or permission_row.status='authorized')
               order by attempt.created_at,attempt.id limit 4""",
            (account_scope,),
        ).fetchall()
        poll = conn.execute(
            """select id from project_las_analysis_attempt
               where record_mode='live_pre_dispatch' and provider='volcengine_las'
                 and account_scope=%s and status in ('submitted','running','unknown')
                 and provider_task_ref is not null and submit_job_ref is not null
                 and provider_task_recorded_at is not null
                 and (
                   (provider_task_recorded_at > now()-interval '70 hours'
                    and updated_at < now()-interval '10 minutes')
                   or (provider_task_recorded_at <= now()-interval '70 hours'
                       and error_code is distinct from 'provider_task_expiry_near')
                 )
               order by updated_at,id limit 8""",
            (account_scope,),
        ).fetchall()
    return [str(row[0]) for row in dispatch], [str(row[0]) for row in poll]


def _unresolved_claims(dsn: str, account_scope: str) -> tuple[list[str], int]:
    """Quarantine abandoned claims, never treat them as permission to Submit again.

    A live Submit holds the attempt row lock. SKIP LOCKED keeps the scheduler
    from changing or waiting on an in-flight provider call. Once the worker
    dies, the durable claim becomes an unknown outcome after fifteen minutes.
    """
    with psycopg.connect(dsn) as conn:
        conn.execute(
            """with stale as (
                 select id from project_las_analysis_attempt
                  where record_mode='live_pre_dispatch' and provider='volcengine_las'
                    and account_scope=%s and status='submitting'
                    and submission_count=1 and provider_task_ref is null
                    and updated_at < now()-interval '15 minutes'
                  order by updated_at,id for update skip locked limit 20
               )
               update project_las_analysis_attempt attempt
                  set status='unknown',error_code='stale_submit_claim_requires_reconciliation'
                 from stale where attempt.id=stale.id""",
            (account_scope,),
        )
        rows = conn.execute(
            """select id from project_las_analysis_attempt
                where record_mode='live_pre_dispatch' and provider='volcengine_las'
                  and account_scope=%s and status='unknown'
                  and submission_count=1 and provider_task_ref is null
                order by updated_at,id limit 20""",
            (account_scope,),
        ).fetchall()
        total = conn.execute(
            """select count(*) from project_las_analysis_attempt
                where record_mode='live_pre_dispatch' and provider='volcengine_las'
                  and account_scope=%s and status='unknown'
                  and submission_count=1 and provider_task_ref is null""",
            (account_scope,),
        ).fetchone()[0]
    return [str(row[0]) for row in rows], total


def main() -> dict:
    import wmill

    if os.environ.get("WM_SCHEDULE_PATH") != _SCHEDULE_PATH:
        raise PermissionError("project LAS batch is schedule-only")
    worker_email = wmill.get_variable("f/content_research/project_las_worker_email")
    actual_actor = (os.environ.get("WM_END_USER_EMAIL") or os.environ.get("WM_EMAIL") or "").strip().lower()
    if not isinstance(worker_email, str) or actual_actor != worker_email.strip().lower():
        raise PermissionError("project LAS schedule identity mismatch")
    db = wmill.get_resource("f/content_research/research_db")
    dsn = make_conninfo(
        host=db["host"], port=int(db.get("port", 5432)), user=db["user"],
        password=db["password"], dbname=db["dbname"],
        sslmode=db.get("sslmode", "prefer"),
    )
    account_scope = wmill.get_variable("f/content_research/project_las_account_scope")
    if not isinstance(account_scope, str) or not _SCOPE.fullmatch(account_scope):
        raise RuntimeError("project LAS account scope unavailable")
    with _single_runner(dsn, account_scope) as acquired:
        if not acquired:
            return {"selected_dispatch": 0, "selected_poll": 0,
                    "results": [], "skipped_overlapping": True}
        return _run_batch(dsn, account_scope, wmill)


def _run_batch(dsn: str, account_scope: str, wmill) -> dict:
    unresolved_claims, unresolved_count = _unresolved_claims(dsn, account_scope)
    to_dispatch, to_poll = _pending(dsn, account_scope)
    results: list[dict] = []
    for attempt_id in to_dispatch:
        try:
            result = wmill.run_script(path=_DISPATCH_PATH, args={"attempt_id": attempt_id},
                                      timeout=180, verbose=False)
            results.append({"attempt_id": attempt_id, "phase": "dispatch",
                            "status": result.get("status", "unknown")})
        except Exception:
            results.append({"attempt_id": attempt_id, "phase": "dispatch", "status": "needs_attention"})
    for attempt_id in to_poll:
        try:
            result = wmill.run_script(path=_POLL_PATH, args={"attempt_id": attempt_id},
                                      timeout=180, verbose=False)
            results.append({"attempt_id": attempt_id, "phase": "poll",
                            "status": result.get("status", "unknown")})
        except Exception:
            results.append({"attempt_id": attempt_id, "phase": "poll", "status": "needs_attention"})
    healthy = {"submitted", "running", "completed", "cancelled"}
    terminal_failures = [f'{row["phase"]}:{row["attempt_id"]}:failed'
                         for row in results if row["status"] == "failed"]
    unresolved = [f'{row["phase"]}:{row["attempt_id"]}:{row["status"]}'
                  for row in results if row["status"] not in healthy | {"failed"}]
    unresolved.extend(f'reconcile:{attempt_id}:unknown_no_task_ref'
                      for attempt_id in unresolved_claims)
    if unresolved_count > len(unresolved_claims):
        unresolved.append(f'reconcile:additional_count:{unresolved_count-len(unresolved_claims)}')
    if unresolved:
        # Only durable attempt UUIDs and phase names enter scheduler logs.
        # Never include the caught exception, signed URL, or supplier key.
        message = "project LAS batch needs reconciliation: " + ", ".join(unresolved)
        if terminal_failures:
            message += "; known terminal failures: " + ", ".join(terminal_failures)
        raise RuntimeError(message)
    if terminal_failures:
        # A failed attempt has a known terminal outcome (or a pre-HTTP refusal).
        # It needs operational attention, but never an unknown-cost reconciliation.
        raise RuntimeError("project LAS batch known terminal failures: " +
                           ", ".join(terminal_failures))
    return {"selected_dispatch": len(to_dispatch), "selected_poll": len(to_poll),
            "results": results}
