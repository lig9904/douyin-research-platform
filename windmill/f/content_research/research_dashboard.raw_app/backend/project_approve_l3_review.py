# /// script
# requires-python = "==3.14.*"
# dependencies = ["douyin-research-platform @ git+https://github.com/lig9904/douyin-research-platform@31b3295c2aa3801eaf900b2a47a542dadb54de5c", "psycopg[binary]==3.3.6", "wmill==1.815.0"]
# ///
"""Save/revoke an exact project L3 review; no provider configuration is accepted."""
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


def main(db: postgresql, reviewer_allowlist: str, action: str, project_id: str, video_id: str,
         transcript_id: str, review_version: str, evidence_fingerprint: str, review_id: str = ""):
    actor = os.environ.get("WM_END_USER_EMAIL")
    service = ProjectL3ReviewService(_dsn(db))
    try:
        if action == "revoke":
            if not review_id:
                raise ValueError("review_id required")
            service.revoke(actor=actor or "", reviewer_allowlist=reviewer_allowlist,
                           project_id=project_id, video_id=video_id, review_id=review_id)
            return {"status": "review_revoked", "external_calls": 0, "llm_calls": 0,
                    "db_writes": 1, "paid_execution_available": False}
        if action != "approve":
            raise ValueError("unsupported action")
        evidence = service.prepare(actor=actor or "", reviewer_allowlist=reviewer_allowlist,
                                   project_id=project_id, video_id=video_id, transcript_id=transcript_id,
                                   review_version=review_version)
        if evidence.fingerprint != evidence_fingerprint:
            raise ValueError("candidate stale")
        receipt = service.approve(actor=actor or "", reviewer_allowlist=reviewer_allowlist, evidence=evidence)
        return {"status": "review_saved", "review_id": str(receipt.review_id),
                "evidence_fingerprint": receipt.evidence_fingerprint, "review_version": receipt.review_version,
                "idempotent_replay": receipt.idempotent_replay, "external_calls": 0, "llm_calls": 0,
                "db_writes": 0 if receipt.idempotent_replay else 1, "paid_execution_available": False}
    except PermissionError:
        raise PermissionError("PROJECT_L3_REVIEW_ACCESS_DENIED") from None
    except Exception:
        raise RuntimeError("PROJECT_L3_REVIEW_WRITE_FAILED") from None
