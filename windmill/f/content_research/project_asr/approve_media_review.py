# /// script
# requires-python = "==3.14.*"
# dependencies = ["douyin-research-platform @ git+https://github.com/lig9904/douyin-research-platform@34ce0706b2b937c04bac732413d5809ac271ed9c", "psycopg[binary]==3.3.6", "wmill==1.815.0"]
# ///
"""Human approval endpoint; identity comes only from WM_END_USER_EMAIL."""
from uuid import UUID
from psycopg.conninfo import make_conninfo
from douyin_research.project_analysis.asr_backend import ProjectASRService, ProjectMediaReviewInput

def _dsn_origin():
    import json, wmill
    db = wmill.get_resource("f/content_research/research_db")
    dsn = make_conninfo(host=db["host"], port=db.get("port",5432), user=db["user"], password=db["password"], dbname=db["dbname"], sslmode=db.get("sslmode","prefer"))
    return dsn, json.loads(wmill.get_variable("f/content_research/media_storage_config"))["public_endpoint"]

def main(project_id: str, video_id: str, asset_id: str, review_version: str, expected_manifest_fingerprint: str, consent_statement: str) -> dict:
    if not isinstance(consent_statement, str) or len(consent_statement.strip()) < 8:
        raise ValueError("explicit human consent statement is required")
    dsn, origin = _dsn_origin()
    return ProjectASRService(dsn, delivery_origin=origin).approve(
        ProjectMediaReviewInput(UUID(project_id), UUID(video_id), UUID(asset_id), review_version, {"consent_statement": consent_statement.strip()}),
        expected_manifest_fingerprint=expected_manifest_fingerprint,
    )
