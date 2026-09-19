"""Replay-safe deterministic features derived from stored comment evidence."""

from __future__ import annotations

import hashlib
import json
import statistics
from collections import Counter
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb


COMMENT_FEATURE_VERSION = "comment-features-v1.0.0"


@dataclass(frozen=True, slots=True)
class CommentFeatureSnapshot:
    snapshot_id: int
    video_id: UUID
    feature_version: str
    evidence_fingerprint: str
    created: bool
    sampled_comment_count: int
    root_comment_count: int
    sampled_reply_count: int
    source_observation_count: int
    text_present_count: int
    question_text_count: int
    like_known_count: int
    like_sum: int | None
    like_median: float | None
    reply_known_count: int
    reply_sum: int | None
    reply_median: float | None
    mean_text_length: float | None
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _CommentEvidence:
    provider: str
    platform_comment_id: str | None
    parent_platform_comment_id: str | None
    text_content: str | None
    like_count: int | None
    reply_count: int | None
    sample_reason: str
    observation_keys: tuple[str, ...]


class CommentFeatureExtractor:
    """Create immutable aggregate snapshots from the evidence dataset.

    These features describe only the collected sample. They do not estimate
    whole-platform sentiment, audience opinion, or causal performance.
    """

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def extract(self, video_id: UUID | str) -> CommentFeatureSnapshot:
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "select id from source_video where id=%s",
                (video_id,),
            )
            video_row = cur.fetchone()
            if video_row is None:
                raise ValueError("source video does not exist")
            canonical_video_id = video_row[0]

            evidence = self._load(cur, canonical_video_id)
            if not evidence:
                raise ValueError("comment evidence is required for L2 features")

            features = _calculate(evidence)
            fingerprint = _fingerprint(evidence)
            cur.execute(
                """
                insert into video_comment_feature_snapshot(
                  video_id, feature_version, evidence_fingerprint,
                  sampled_comment_count, root_comment_count, sampled_reply_count,
                  source_observation_count, text_present_count,
                  question_text_count, like_known_count, like_sum, like_median,
                  reply_known_count, reply_sum, reply_median, mean_text_length,
                  metadata
                )
                values (
                  %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                )
                on conflict(video_id, feature_version, evidence_fingerprint)
                do nothing
                returning id
                """,
                (
                    canonical_video_id,
                    COMMENT_FEATURE_VERSION,
                    fingerprint,
                    features["sampled_comment_count"],
                    features["root_comment_count"],
                    features["sampled_reply_count"],
                    features["source_observation_count"],
                    features["text_present_count"],
                    features["question_text_count"],
                    features["like_known_count"],
                    features["like_sum"],
                    features["like_median"],
                    features["reply_known_count"],
                    features["reply_sum"],
                    features["reply_median"],
                    features["mean_text_length"],
                    Jsonb(features["metadata"]),
                ),
            )
            inserted = cur.fetchone()
            created = inserted is not None
            if inserted is None:
                cur.execute(
                    """
                    select id
                    from video_comment_feature_snapshot
                    where video_id=%s
                      and feature_version=%s
                      and evidence_fingerprint=%s
                    """,
                    (canonical_video_id, COMMENT_FEATURE_VERSION, fingerprint),
                )
                inserted = cur.fetchone()
                if inserted is None:
                    raise RuntimeError("comment feature snapshot conflict could not be resolved")

            cur.execute(
                """
                update source_video
                set research_level=greatest(research_level, 2)
                where id=%s
                """,
                (canonical_video_id,),
            )
            conn.commit()

        return CommentFeatureSnapshot(
            snapshot_id=inserted[0],
            video_id=canonical_video_id,
            feature_version=COMMENT_FEATURE_VERSION,
            evidence_fingerprint=fingerprint,
            created=created,
            sampled_comment_count=features["sampled_comment_count"],
            root_comment_count=features["root_comment_count"],
            sampled_reply_count=features["sampled_reply_count"],
            source_observation_count=features["source_observation_count"],
            text_present_count=features["text_present_count"],
            question_text_count=features["question_text_count"],
            like_known_count=features["like_known_count"],
            like_sum=features["like_sum"],
            like_median=features["like_median"],
            reply_known_count=features["reply_known_count"],
            reply_sum=features["reply_sum"],
            reply_median=features["reply_median"],
            mean_text_length=features["mean_text_length"],
            metadata=features["metadata"],
        )

    @staticmethod
    def _load(cur, video_id: UUID) -> list[_CommentEvidence]:
        cur.execute(
            """
            select
              c.provider,
              c.platform_comment_id,
              c.parent_platform_comment_id,
              c.text_content,
              c.like_count,
              c.reply_count,
              c.sample_reason,
              coalesce(
                array_agg(o.observation_key order by o.observation_key)
                  filter (where o.observation_key is not null),
                array[]::text[]
              )
            from video_comment c
            left join video_comment_observation o on o.comment_id=c.id
            where c.video_id=%s
            group by
              c.id, c.provider, c.platform_comment_id,
              c.parent_platform_comment_id, c.text_content,
              c.like_count, c.reply_count, c.sample_reason
            order by c.provider, c.platform_comment_id nulls last, c.id
            """,
            (video_id,),
        )
        return [
            _CommentEvidence(
                provider=row[0],
                platform_comment_id=row[1],
                parent_platform_comment_id=row[2],
                text_content=row[3],
                like_count=row[4],
                reply_count=row[5],
                sample_reason=row[6],
                observation_keys=tuple(row[7]),
            )
            for row in cur.fetchall()
        ]


def _calculate(evidence: list[_CommentEvidence]) -> dict[str, Any]:
    texts = [
        item.text_content.strip()
        for item in evidence
        if item.text_content is not None and item.text_content.strip()
    ]
    likes = [item.like_count for item in evidence if item.like_count is not None]
    replies = [item.reply_count for item in evidence if item.reply_count is not None]
    provider_counts = Counter(item.provider for item in evidence)
    reason_counts = Counter(item.sample_reason for item in evidence)

    return {
        "sampled_comment_count": len(evidence),
        "root_comment_count": sum(
            item.parent_platform_comment_id is None for item in evidence
        ),
        "sampled_reply_count": sum(
            item.parent_platform_comment_id is not None for item in evidence
        ),
        "source_observation_count": sum(
            len(item.observation_keys) for item in evidence
        ),
        "text_present_count": len(texts),
        "question_text_count": sum(
            "?" in text or "？" in text for text in texts
        ),
        "like_known_count": len(likes),
        "like_sum": sum(likes) if likes else None,
        "like_median": float(statistics.median(likes)) if likes else None,
        "reply_known_count": len(replies),
        "reply_sum": sum(replies) if replies else None,
        "reply_median": float(statistics.median(replies)) if replies else None,
        "mean_text_length": (
            sum(len(text) for text in texts) / len(texts) if texts else None
        ),
        "metadata": {
            "provider_counts": dict(sorted(provider_counts.items())),
            "sample_reason_counts": dict(sorted(reason_counts.items())),
            "text_length_unit": "unicode_codepoints",
            "population_scope": "collected_sample_only",
            "semantic_inference": False,
            "llm_calls": 0,
        },
    }


def _fingerprint(evidence: list[_CommentEvidence]) -> str:
    payload = [
        {
            "provider": item.provider,
            "platform_comment_id": item.platform_comment_id,
            "parent_platform_comment_id": item.parent_platform_comment_id,
            "text_content": item.text_content,
            "like_count": item.like_count,
            "reply_count": item.reply_count,
            "sample_reason": item.sample_reason,
            "observation_keys": list(item.observation_keys),
        }
        for item in evidence
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
