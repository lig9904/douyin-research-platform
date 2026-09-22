# /// script
# requires-python = "==3.14.*"
# dependencies = [
#   "douyin-research-platform @ git+https://github.com/lig9904/douyin-research-platform@ab1255f97e2a05e226a24e3a47ac8c52398b9296",
#   "psycopg[binary]==3.3.6", "wmill==1.815.0",
# ]
# ///
"""Resume only persisted submitted/running ASR jobs; never discover new work."""
from uuid import UUID
import psycopg
from psycopg.conninfo import make_conninfo
from douyin_research.l2.asr_execution import MAX_SCHEDULED_POLLS, MAX_SCHEDULED_AGE_SECONDS
from douyin_research.reviewed_dispatch import scheduler_slot

WORKER_PATH = "f/content_research/analysis/run_reviewed_asr"
BATCH_SIZE = 5


def _pending(dsn):
    with psycopg.connect(dsn) as conn:
        conn.execute("set transaction read only")
        base = """from asr_execution_job
            where provider='volcengine-doubao-asr' and task_key like 'live-asr:%%'
            and status in ('submitted','running') and provider_task_ref is not null
            and submission_count=1"""
        eligible = """poll_count < coalesce((metadata->>'poll_limit')::integer,%s)
            and created_at + make_interval(secs => coalesce(
                (metadata->>'poll_max_age_seconds')::integer,%s)) > now()"""
        policy = (MAX_SCHEDULED_POLLS, MAX_SCHEDULED_AGE_SECONDS)
        rows = conn.execute("select id,video_id,metadata " + base + " and (" + eligible + ") order by updated_at,id limit %s",
            (*policy, BATCH_SIZE)).fetchall()
        limited = conn.execute("select count(*) " + base + " and not (" + eligible + ")", policy).fetchone()[0]
        return rows, limited


def main() -> dict:
    import wmill
    try:
        db = wmill.get_resource("f/content_research/research_db")
        dsn = make_conninfo(host=db["host"], port=db.get("port", 5432), user=db["user"],
            password=db["password"], dbname=db["dbname"], sslmode=db.get("sslmode", "prefer"))
    except Exception:
        raise RuntimeError("ASR pending jobs unavailable") from None
    try:
        with scheduler_slot(dsn) as acquired:
            if not acquired:
                return {"status": "deferred", "reason": "analysis_scheduler_busy"}
            return _run_pending(dsn, wmill)
    except psycopg.Error:
        raise RuntimeError("ASR pending jobs unavailable") from None


def _run_pending(dsn, wmill):
    rows, limited = _pending(dsn)
    completed = pending = failed = 0
    for job_id, video_id, metadata in rows:
        try:
            asset = str(UUID(metadata["reviewed_asset_id"]))
            version = metadata["media_review_version"]
            if not isinstance(version, str) or not version.strip():
                raise ValueError
            result = wmill.run_script(path=WORKER_PATH, args={
                "video_id": str(video_id), "asset_id": asset, "review_version": version,
                "resume_job_id": str(job_id)}, timeout=310, verbose=False)
            if result.get("error_code"):
                failed += 1
            elif result.get("status") == "completed":
                completed += 1
            elif result.get("status") in {"submitted", "running"}:
                pending += 1
            else:
                failed += 1
        except Exception:
            failed += 1
    if failed or limited:
        raise RuntimeError(f"ASR polling requires attention: completed={completed}, pending={pending}, failed={failed}, limited={limited}") from None
    return {"selected": len(rows), "completed": completed, "pending": pending, "failed": 0, "limited": 0}
