# /// script
# requires-python = "==3.14.*"
# dependencies = ["douyin-research-platform @ git+https://github.com/lig9904/douyin-research-platform@34ce0706b2b937c04bac732413d5809ac271ed9c", "psycopg[binary]==3.3.6", "wmill==1.815.0"]
# ///
"""Fixed batch recovery for submitted project ASR work; never discovers reviews."""
from __future__ import annotations

import psycopg
from psycopg.conninfo import make_conninfo

from douyin_research.reviewed_dispatch import scheduler_slot

_WORKER_PATH = "f/content_research/project_asr/poll_reviewed_asr"
_BATCH_SIZE = 12
_MAX_POLLS = 12
_MAX_AGE_SECONDS = 86_400


def _pending(dsn):
    with psycopg.connect(dsn) as conn:
        conn.execute("set transaction read only")
        base = """from project_asr_execution_job
                  where status in ('submitted','running') and provider_task_ref is not null and submission_count=1"""
        eligible = "poll_count < %s and created_at + make_interval(secs => %s) > now()"
        rows = conn.execute("select project_id,task_key " + base + " and " + eligible +
                            " order by updated_at,id limit %s", (_MAX_POLLS, _MAX_AGE_SECONDS, _BATCH_SIZE)).fetchall()
        limited = conn.execute("select count(*) " + base + " and not (" + eligible + ")",
                               (_MAX_POLLS, _MAX_AGE_SECONDS)).fetchone()[0]
        return rows, limited


def main() -> dict:
    import wmill
    try:
        db = wmill.get_resource("f/content_research/research_db")
        dsn = make_conninfo(host=db["host"], port=int(db.get("port", 5432)), user=db["user"],
                            password=db["password"], dbname=db["dbname"], sslmode=db.get("sslmode", "prefer"))
        with scheduler_slot(dsn) as acquired:
            if not acquired:
                return {"status": "deferred", "reason": "analysis_scheduler_busy"}
            rows, limited = _pending(dsn)
            completed = pending = failed = 0
            for project_id, task_key in rows:
                result = wmill.run_script(path=_WORKER_PATH, args={"project_id": str(project_id), "task_key": task_key},
                                          timeout=90, verbose=False)
                if result.get("status") == "completed":
                    completed += 1
                elif result.get("status") in {"submitted", "running"}:
                    pending += 1
                else:
                    failed += 1
    except Exception:
        raise RuntimeError("project ASR pending polling unavailable") from None
    if failed or limited:
        raise RuntimeError(f"project ASR polling requires attention: completed={completed}, pending={pending}, failed={failed}, limited={limited}")
    return {"selected": len(rows), "completed": completed, "pending": pending, "failed": 0, "limited": 0}
