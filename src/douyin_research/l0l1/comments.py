"""Idempotent normalized comment/reply evidence ingestion."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from douyin_research.providers.types import CommentSample


@dataclass(slots=True)
class CommentIngestContext:
    platform: str
    video_platform_id: str
    provider: str
    request_fingerprint: str
    run_id: UUID | None = None


@dataclass(slots=True)
class CommentIngestResult:
    comment_ids: list[UUID]
    new_comments: int
    observations_inserted: int
    duplicate_observations: int
    duplicate_input_items: int


class CommentEvidenceStore:
    """Store canonical comments plus replay-safe per-response observations."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def ingest(
        self,
        samples: Iterable[CommentSample],
        context: CommentIngestContext,
    ) -> CommentIngestResult:
        if not context.platform:
            raise ValueError("context.platform is required")
        if not context.video_platform_id:
            raise ValueError("context.video_platform_id is required")
        if not context.provider:
            raise ValueError("context.provider is required")
        if not context.request_fingerprint:
            raise ValueError("context.request_fingerprint is required")

        unique: dict[tuple[str, str], CommentSample] = {}
        duplicate_input_items = 0
        for sample in samples:
            self._validate(sample, context)
            key = (sample.provider, sample.platform_comment_id)
            if key in unique:
                duplicate_input_items += 1
                continue
            unique[key] = sample

        comment_ids: list[UUID] = []
        new_comments = 0
        observations_inserted = 0

        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                select id
                from source_video
                where platform=%s and platform_video_id=%s
                """,
                (context.platform, context.video_platform_id),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError("source video must exist before comment ingestion")
            video_id = row[0]

            for sample in unique.values():
                observed_at = sample.observed_at or _utcnow()
                cur.execute(
                    """
                    insert into video_comment(
                      video_id, provider, source_endpoint, platform_comment_id,
                      parent_platform_comment_id, text_content, like_count,
                      reply_count, sample_reason, raw_ref, published_at,
                      captured_at, last_seen_at, observation_count
                    )
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0)
                    on conflict(provider, video_id, platform_comment_id)
                      where platform_comment_id is not null
                    do update set
                      source_endpoint=excluded.source_endpoint,
                      parent_platform_comment_id=coalesce(
                        excluded.parent_platform_comment_id,
                        video_comment.parent_platform_comment_id
                      ),
                      text_content=coalesce(excluded.text_content, video_comment.text_content),
                      published_at=coalesce(video_comment.published_at, excluded.published_at),
                      sample_reason=excluded.sample_reason,
                      raw_ref=coalesce(excluded.raw_ref, video_comment.raw_ref),
                      last_seen_at=greatest(video_comment.last_seen_at, excluded.last_seen_at)
                    returning id, (xmax = 0) as inserted
                    """,
                    (
                        video_id,
                        sample.provider,
                        sample.source_endpoint,
                        sample.platform_comment_id,
                        sample.parent_platform_comment_id,
                        sample.text,
                        sample.like_count,
                        sample.reply_count,
                        sample.sample_reason,
                        sample.raw_ref,
                        sample.published_at,
                        observed_at,
                        observed_at,
                    ),
                )
                comment_id, inserted = cur.fetchone()
                comment_ids.append(comment_id)
                new_comments += int(inserted)

                observation_key = _observation_key(
                    sample.raw_ref or context.request_fingerprint,
                    context.platform,
                    context.video_platform_id,
                    sample.provider,
                    sample.platform_comment_id,
                )
                cur.execute(
                    """
                    insert into video_comment_observation(
                      comment_id, observation_key, provider, source_endpoint,
                      request_fingerprint, observed_at, like_count, reply_count,
                      raw_ref, metadata
                    )
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    on conflict(observation_key) do nothing
                    returning id
                    """,
                    (
                        comment_id,
                        observation_key,
                        sample.provider,
                        sample.source_endpoint,
                        context.request_fingerprint,
                        observed_at,
                        sample.like_count,
                        sample.reply_count,
                        sample.raw_ref,
                        Jsonb(
                            {
                                "sample_reason": sample.sample_reason,
                                "is_reply": sample.parent_platform_comment_id is not None,
                            }
                        ),
                    ),
                )
                observation_inserted = cur.fetchone() is not None
                if observation_inserted:
                    observations_inserted += 1
                    cur.execute(
                        """
                        update video_comment
                        set like_count=coalesce(%s, like_count),
                            reply_count=coalesce(%s, reply_count),
                            raw_ref=coalesce(%s, raw_ref),
                            last_seen_at=greatest(last_seen_at, %s),
                            observation_count=observation_count+1
                        where id=%s
                        """,
                        (
                            sample.like_count,
                            sample.reply_count,
                            sample.raw_ref,
                            observed_at,
                            comment_id,
                        ),
                    )

                if context.run_id is not None:
                    cur.execute(
                        """
                        insert into pipeline_run_item(
                          run_id, entity_type, entity_id, stage, outcome,
                          reason_code, metadata
                        )
                        values (%s,'comment',%s,'L2','sampled',null,%s)
                        on conflict(run_id, entity_type, entity_id)
                        do update set metadata =
                          pipeline_run_item.metadata || excluded.metadata
                        """,
                        (
                            context.run_id,
                            comment_id,
                            Jsonb(
                                {
                                    "platform": context.platform,
                                    "source_endpoint": sample.source_endpoint,
                                    "sample_reason": sample.sample_reason,
                                    "observation_inserted": observation_inserted,
                                }
                            ),
                        ),
                    )

            conn.commit()

        return CommentIngestResult(
            comment_ids=list(dict.fromkeys(comment_ids)),
            new_comments=new_comments,
            observations_inserted=observations_inserted,
            duplicate_observations=len(unique) - observations_inserted,
            duplicate_input_items=duplicate_input_items,
        )

    @staticmethod
    def _validate(sample: CommentSample, context: CommentIngestContext) -> None:
        if sample.platform != context.platform:
            raise ValueError(
                f"comment platform {sample.platform} != context platform {context.platform}"
            )
        if sample.video_platform_id != context.video_platform_id:
            raise ValueError("comment video ID does not match ingestion context")
        if sample.provider != context.provider:
            raise ValueError(
                f"comment provider {sample.provider} != context provider {context.provider}"
            )
        if not sample.platform_comment_id:
            raise ValueError("platform_comment_id is required")


def _observation_key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
