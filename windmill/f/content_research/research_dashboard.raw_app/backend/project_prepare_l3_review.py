# py: ==3.14.*
"""Controlled project L3 review preparation; this endpoint never calls a model."""
from __future__ import annotations

import os
from typing import TypedDict

from psycopg.conninfo import make_conninfo
from douyin_research.project_analysis.l3 import ProjectL3ReviewService


class postgresql(TypedDict):
    host: str
    port: int
    user: str
    password: str
    dbname: str
    sslmode: str


def _dsn(db: postgresql) -> str:
    return make_conninfo(host=db["host"], port=int(db.get("port", 5432)), user=db["user"],
                         password=db["password"], dbname=db["dbname"], sslmode=db.get("sslmode", "prefer"))


def main(db: postgresql, reviewer_allowlist: str, project_id: str, video_id: str,
         transcript_id: str, review_version: str):
    # Both actor and allowlist originate in Windmill, never in Raw App fields.
    actor = os.environ.get("WM_END_USER_EMAIL")
    try:
        evidence = ProjectL3ReviewService(_dsn(db)).prepare(
            actor=actor or "", reviewer_allowlist=reviewer_allowlist, project_id=project_id,
            video_id=video_id, transcript_id=transcript_id, review_version=review_version,
        )
    except PermissionError:
        raise PermissionError("PROJECT_L3_REVIEW_ACCESS_DENIED") from None
    except Exception:
        raise RuntimeError("PROJECT_L3_REVIEW_CANDIDATE_UNAVAILABLE") from None
    # This is deliberately the controlled reviewer channel. Its Windmill job
    # result contains the exact body being approved; callers must not expose it
    # through a general project-reader endpoint or MCP.
    return {"status": "review_candidate", "project_id": str(evidence.project_id),
            "video_id": str(evidence.video_id), "transcript_id": str(evidence.transcript_id),
            "review_version": evidence.review_version, "evidence_fingerprint": evidence.fingerprint,
            "evidence_version": "project-l3-evidence-v1.0.0", "evidence_modalities": list(evidence.modalities),
            "evidence_bundle": evidence.bundle, "external_calls": 0, "llm_calls": 0,
            "db_writes": 0, "paid_execution_available": False}
