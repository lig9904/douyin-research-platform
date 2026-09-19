#requirements:
#douyin-research-platform @ git+https://github.com/lig9904/douyin-research-platform@1558677c40cc22239e660e269738619dfd05388d

from __future__ import annotations

import os
from typing import TypedDict

from psycopg.conninfo import make_conninfo

from douyin_research.l3 import L3ReviewService, authorize_reviewer


class postgresql(TypedDict):
    host: str
    port: int
    user: str
    password: str
    dbname: str
    sslmode: str


def _dsn(db: postgresql) -> str:
    return make_conninfo(
        host=db["host"],
        port=int(db.get("port", 5432)),
        user=db["user"],
        password=db["password"],
        dbname=db["dbname"],
        sslmode=db.get("sslmode", "prefer"),
    )


def main(
    db: postgresql,
    reviewer_allowlist: str,
    video_id: str,
    privacy_review_version: str,
):
    authorize_reviewer(os.environ.get("WM_END_USER_EMAIL"), reviewer_allowlist)
    try:
        manifest = L3ReviewService(_dsn(db)).prepare(
            video_id,
            privacy_review_version=privacy_review_version,
        ).as_dict()
    except Exception:
        raise RuntimeError("L3_REVIEW_CANDIDATE_UNAVAILABLE") from None
    return {
        "status": "review_candidate",
        "evidence_fingerprint": manifest["evidence_fingerprint"],
        "evidence_version": manifest["evidence_version"],
        "evidence_modalities": manifest["evidence_modalities"],
        "privacy_review_version": manifest["privacy_review_version"],
        "external_calls": 0,
        "llm_calls": 0,
        "db_writes": 0,
        "raw_evidence_included": False,
        "paid_execution_available": False,
    }
