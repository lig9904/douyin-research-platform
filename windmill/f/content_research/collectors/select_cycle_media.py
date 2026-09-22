# /// script
# requires-python = "==3.14.*"
# dependencies = ["psycopg[binary]==3.3.6", "wmill==1.815.0"]
# ///
"""Select only successful L2 items from one persisted cycle; no external calls."""
from uuid import UUID

import psycopg
from psycopg.conninfo import make_conninfo


def select(dsn: str, run_id: UUID) -> list[str]:
    with psycopg.connect(dsn) as conn:
        conn.execute("set transaction read only")
        run = conn.execute(
            "select run_type,status,platform from pipeline_run where id=%s", (run_id,)
        ).fetchone()
        if run != ("comment_l2_l3_batch", "success", "douyin"):
            raise ValueError("successful comment batch required")
        rows = conn.execute("""select i.entity_id
            from pipeline_run_item i join source_video v on v.id=i.entity_id
            where i.run_id=%s and i.entity_type='video' and i.stage='L2'
              and i.outcome='success' and v.platform='douyin'
            order by i.entity_id limit 6""", (run_id,)).fetchall()
        if len(rows) > 5:
            raise ValueError("cycle media batch exceeds configured discovery size")
        return [str(row[0]) for row in rows]


def main(comment_run_id: str) -> dict:
    identifier = UUID(comment_run_id)
    try:
        import wmill
        db = wmill.get_resource("f/content_research/research_db")
        dsn = make_conninfo(host=db["host"], port=db.get("port", 5432),
            user=db["user"], password=db["password"], dbname=db["dbname"],
            sslmode=db.get("sslmode", "prefer"))
        videos = select(dsn, identifier)
        return {"comment_run_id": str(identifier), "video_ids": videos,
                "selected_count": len(videos), "external_calls": 0}
    except Exception:
        raise RuntimeError("cycle media selection failed; inspect persisted comment batch") from None
