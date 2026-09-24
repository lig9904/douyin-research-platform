# /// script
# requires-python = "==3.14.*"
# dependencies = ["douyin-research-platform", "psycopg[binary]==3.3.6", "wmill==1.815.0"]
# ///
"""Schedule-only dispatch of human-approved project audio; never approves it."""
from __future__ import annotations

import psycopg
from psycopg.conninfo import make_conninfo

from douyin_research.reviewed_dispatch import scheduler_slot

_WORKER_PATH = "f/content_research/project_asr/dispatch_reviewed_asr"
_BATCH_SIZE = 12


def _pending(dsn):
    with psycopg.connect(dsn) as conn:
        conn.execute("set transaction read only")
        return conn.execute(
            """select review.project_id,review.video_id,review.id
               from project_asr_media_review review
               join project_video_inclusion inclusion_row
                 on inclusion_row.project_id=review.project_id and inclusion_row.video_id=review.video_id
               join source_video video_row on video_row.id=review.video_id
               join research_project project_row on project_row.id=review.project_id
               join research_organization org_row on org_row.id=project_row.organization_id
               where review.status='approved' and inclusion_row.status='accepted'
                 and video_row.availability_status='available'
                 and project_row.status='active' and org_row.status='active'
                 and not exists (
                   select 1 from project_asr_execution_job job
                   where job.media_review_id=review.id
                 )
               order by review.reviewed_at,review.id limit %s""",
            (_BATCH_SIZE,),
        ).fetchall()


def main() -> dict:
    import wmill

    try:
        db = wmill.get_resource("f/content_research/research_db")
        dsn = make_conninfo(host=db["host"], port=int(db.get("port", 5432)), user=db["user"],
                            password=db["password"], dbname=db["dbname"], sslmode=db.get("sslmode", "prefer"))
        with scheduler_slot(dsn) as acquired:
            if not acquired:
                return {"status": "deferred", "reason": "analysis_scheduler_busy"}
            rows = _pending(dsn)
            submitted = completed = needs_attention = 0
            for project_id, video_id, review_id in rows:
                result = wmill.run_script(
                    path=_WORKER_PATH,
                    args={"project_id": str(project_id), "video_id": str(video_id),
                          "media_review_id": str(review_id)},
                    timeout=90, verbose=False,
                )
                if result.get("status") in {"submitted", "running"}:
                    submitted += 1
                elif result.get("status") == "completed":
                    completed += 1
                else:
                    needs_attention += 1
    except Exception:
        raise RuntimeError("project ASR reviewed dispatch unavailable") from None
    if needs_attention:
        raise RuntimeError(f"project ASR reviewed dispatch requires attention: {needs_attention}")
    return {"selected": len(rows), "submitted": submitted, "completed": completed,
            "needs_attention": 0}
