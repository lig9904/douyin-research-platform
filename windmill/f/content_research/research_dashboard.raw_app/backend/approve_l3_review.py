#requirements:
#douyin-research-platform @ git+https://github.com/lig9904/douyin-research-platform@1558677c40cc22239e660e269738619dfd05388d

from __future__ import annotations

import os
from typing import TypedDict

from psycopg.conninfo import make_conninfo

from douyin_research.l3 import (
    L3ApprovalRequest,
    L3CandidateStaleError,
    L3IdempotencyConflictError,
    L3ReviewService,
    authorize_reviewer,
)


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
    evidence_fingerprint: str,
    evidence_version: str,
    evidence_modalities: list[str],
    idempotency_key: str,
):
    actor = authorize_reviewer(
        os.environ.get("WM_END_USER_EMAIL"),
        reviewer_allowlist,
    )
    try:
        receipt = L3ReviewService(_dsn(db)).approve(
            L3ApprovalRequest(
                video_id=video_id,
                actor=actor,
                idempotency_key=idempotency_key,
                evidence_fingerprint=evidence_fingerprint,
                evidence_version=evidence_version,
                evidence_modalities=tuple(evidence_modalities),
                privacy_review_version=privacy_review_version,
            )
        ).as_dict()
    except L3CandidateStaleError:
        raise RuntimeError("L3_REVIEW_CANDIDATE_STALE") from None
    except L3IdempotencyConflictError:
        raise RuntimeError("L3_REVIEW_IDEMPOTENCY_CONFLICT") from None
    except ValueError:
        raise RuntimeError("L3_REVIEW_INPUT_INVALID") from None
    except Exception:
        raise RuntimeError("L3_REVIEW_APPROVAL_FAILED") from None
    return {
        "status": "review_saved",
        "approved_at": receipt["approved_at"],
        "idempotent_replay": receipt["idempotent_replay"],
        "evidence_fingerprint": receipt["evidence_fingerprint"],
        "evidence_version": receipt["evidence_version"],
        "evidence_modalities": receipt["evidence_modalities"],
        "privacy_review_version": receipt["privacy_review_version"],
        "external_calls": 0,
        "llm_calls": 0,
        "db_writes": 0 if receipt["idempotent_replay"] else 1,
        "raw_evidence_included": False,
        "paid_execution_available": False,
    }
