from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import UUID

import psycopg
import pytest

from douyin_research.l0l1 import (
    CommentEvidenceStore,
    CommentIngestContext,
)
from douyin_research.l2 import CommentFeatureExtractor
from douyin_research.providers.types import CommentSample


DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not configured")
VIDEO_PLATFORM_ID = "synthetic-video-l2"


def _clear() -> None:
    assert DSN
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("delete from source_video")
        conn.commit()


def _insert_video() -> UUID:
    assert DSN
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into source_video(platform, platform_video_id)
            values ('douyin', %s)
            returning id
            """,
            (VIDEO_PLATFORM_ID,),
        )
        video_id = cur.fetchone()[0]
        conn.commit()
    return video_id


def _sample(
    comment_id: str,
    *,
    text: str | None,
    like_count: int | None,
    reply_count: int | None,
    raw_ref: str,
    parent_id: str | None = None,
) -> CommentSample:
    return CommentSample(
        provider="tikhub",
        platform="douyin",
        source_endpoint=(
            "douyin.app.comment_replies"
            if parent_id is not None
            else "douyin.app.comments"
        ),
        video_platform_id=VIDEO_PLATFORM_ID,
        platform_comment_id=comment_id,
        text=text,
        parent_platform_comment_id=parent_id,
        like_count=like_count,
        reply_count=reply_count,
        sample_reason="thread_root" if parent_id else "top",
        observed_at=datetime(2026, 9, 19, 11, 0, tzinfo=timezone.utc),
        raw_ref=raw_ref,
    )


def _ingest(samples, fingerprint: str) -> None:
    assert DSN
    CommentEvidenceStore(DSN).ingest(
        samples,
        CommentIngestContext(
            platform="douyin",
            video_platform_id=VIDEO_PLATFORM_ID,
            provider="tikhub",
            request_fingerprint=fingerprint,
        ),
    )


def test_features_are_replay_safe_and_preserve_null_vs_zero() -> None:
    assert DSN
    _clear()
    video_id = _insert_video()
    _ingest(
        [
            _sample(
                "synthetic-comment-1",
                text="真的吗？",
                like_count=0,
                reply_count=None,
                raw_ref="external_api_response:synthetic-1",
            ),
            _sample(
                "synthetic-comment-2",
                text=None,
                like_count=None,
                reply_count=0,
                raw_ref="external_api_response:synthetic-1",
            ),
            _sample(
                "synthetic-reply-1",
                text="好！",
                like_count=5,
                reply_count=0,
                parent_id="synthetic-comment-1",
                raw_ref="external_api_response:synthetic-2",
            ),
        ],
        "synthetic-fingerprint-1",
    )

    extractor = CommentFeatureExtractor(DSN)
    first = extractor.extract(video_id)
    replay = extractor.extract(video_id)

    assert first.created is True
    assert replay.created is False
    assert replay.snapshot_id == first.snapshot_id
    assert first.sampled_comment_count == 3
    assert first.root_comment_count == 2
    assert first.sampled_reply_count == 1
    assert first.source_observation_count == 3
    assert first.text_present_count == 2
    assert first.question_text_count == 1
    assert first.like_known_count == 2
    assert first.like_sum == 5
    assert first.like_median == pytest.approx(2.5)
    assert first.reply_known_count == 2
    assert first.reply_sum == 0
    assert first.reply_median == 0
    assert first.mean_text_length == pytest.approx(3.0)
    assert first.metadata["population_scope"] == "collected_sample_only"
    assert first.metadata["semantic_inference"] is False
    assert first.metadata["llm_calls"] == 0

    serialized_metadata = repr(first.metadata)
    assert "synthetic-comment" not in serialized_metadata
    assert "external_api_response" not in serialized_metadata
    assert "synthetic-fingerprint" not in serialized_metadata

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select count(*), min(research_level)
            from video_comment_feature_snapshot f
            join source_video v on v.id=f.video_id
            where f.video_id=%s
            """,
            (video_id,),
        )
        assert cur.fetchone() == (1, 2)


def test_new_observation_set_creates_new_snapshot() -> None:
    assert DSN
    _clear()
    video_id = _insert_video()
    base = _sample(
        "synthetic-comment-1",
        text="文本",
        like_count=0,
        reply_count=None,
        raw_ref="external_api_response:synthetic-1",
    )
    _ingest([base], "same-request-params")
    extractor = CommentFeatureExtractor(DSN)
    first = extractor.extract(video_id)

    changed = _sample(
        "synthetic-comment-1",
        text="文本",
        like_count=7,
        reply_count=None,
        raw_ref="external_api_response:synthetic-2",
    )
    _ingest([changed], "same-request-params")
    second = extractor.extract(video_id)

    assert first.created is True
    assert second.created is True
    assert second.snapshot_id != first.snapshot_id
    assert second.evidence_fingerprint != first.evidence_fingerprint
    assert second.source_observation_count == 2
    assert second.like_sum == 7

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "select count(*) from video_comment_feature_snapshot where video_id=%s",
            (video_id,),
        )
        assert cur.fetchone()[0] == 2


def test_all_unknown_metrics_remain_null_and_missing_evidence_stops() -> None:
    assert DSN
    _clear()
    video_id = _insert_video()
    extractor = CommentFeatureExtractor(DSN)

    with pytest.raises(ValueError, match="comment evidence is required"):
        extractor.extract(video_id)

    _ingest(
        [
            _sample(
                "synthetic-comment-unknown",
                text="",
                like_count=None,
                reply_count=None,
                raw_ref="external_api_response:synthetic-unknown",
            )
        ],
        "synthetic-fingerprint-unknown",
    )
    result = extractor.extract(video_id)

    assert result.like_known_count == 0
    assert result.like_sum is None
    assert result.like_median is None
    assert result.reply_known_count == 0
    assert result.reply_sum is None
    assert result.reply_median is None
    assert result.text_present_count == 0
    assert result.mean_text_length is None


def test_text_distribution_is_aggregate_only_and_rule_based() -> None:
    assert DSN
    _clear()
    video_id = _insert_video()
    texts = [
        " 好 看 ",
        "好　看",
        "https://example.invalid 优惠",
        "@synthetic-user 真的好看",
        "😀！！！",
        "哈哈哈哈哈",
        "好",
        "真的好看",
    ]
    _ingest(
        [
            _sample(
                f"synthetic-rule-{index}",
                text=text,
                like_count=None,
                reply_count=None,
                raw_ref="external_api_response:synthetic-rules",
            )
            for index, text in enumerate(texts)
        ],
        "synthetic-fingerprint-rules",
    )

    result = CommentFeatureExtractor(DSN).extract(video_id)

    assert result.feature_version == "comment-features-v1.1.0"
    assert result.eligible_text_count == 4
    assert result.normalized_unique_text_count == 7
    assert result.duplicate_text_count == 1
    assert result.duplicate_group_count == 1
    assert result.max_duplicate_group_size == 2
    assert result.url_text_count == 1
    assert result.mention_text_count == 1
    assert result.emoji_only_text_count == 1
    assert result.repeated_char_text_count == 1
    assert result.short_text_count == 1
    assert result.template_like_text_count == 5
    assert result.char_bigram_count == 8
    assert result.unique_char_bigram_count == 3
    assert result.top_char_bigram_count == 4
    assert result.top_char_bigram_share == pytest.approx(0.5)
    assert result.metadata["stored_terms"] is False
    assert result.metadata["semantic_inference"] is False

    serialized_metadata = repr(result.metadata)
    for raw_text in texts:
        assert raw_text not in serialized_metadata


def test_historical_snapshot_text_fields_remain_null() -> None:
    assert DSN
    _clear()
    video_id = _insert_video()

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into video_comment_feature_snapshot(
              video_id, feature_version, evidence_fingerprint,
              sampled_comment_count, root_comment_count, sampled_reply_count,
              source_observation_count, text_present_count,
              question_text_count, like_known_count,
              reply_known_count, metadata
            ) values (%s, 'comment-features-v1.0.0', %s, 0, 0, 0, 0, 0, 0, 0, 0, '{}')
            returning eligible_text_count, top_char_bigram_share
            """,
            (video_id, "0" * 64),
        )
        assert cur.fetchone() == (None, None)
        conn.commit()
