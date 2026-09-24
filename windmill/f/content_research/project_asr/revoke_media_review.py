# /// script
# requires-python = "==3.14.*"
# dependencies = ["douyin-research-platform", "psycopg[binary]==3.3.6", "wmill==1.815.0"]
# ///
"""Revoke a project-local media approval; identity is server-authenticated."""
import json
from uuid import UUID
from psycopg.conninfo import make_conninfo
from douyin_research.project_analysis.asr_backend import ProjectASRService

def main(project_id: str, media_review_id: str) -> dict:
    import wmill
    db = wmill.get_resource("f/content_research/research_db")
    dsn = make_conninfo(host=db["host"], port=db.get("port",5432), user=db["user"], password=db["password"], dbname=db["dbname"], sslmode=db.get("sslmode","prefer"))
    origin = json.loads(wmill.get_variable("f/content_research/media_storage_config"))["public_endpoint"]
    return ProjectASRService(dsn, delivery_origin=origin).revoke(project_id=UUID(project_id), media_review_id=UUID(media_review_id))
