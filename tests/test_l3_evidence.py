from __future__ import annotations

import os
from datetime import date
from uuid import UUID

import psycopg
import pytest
from psycopg.types.json import Jsonb

from douyin_research.l2 import L3PromotionGate
from douyin_research.l3 import L3_EVIDENCE_VERSION, L3EvidenceAssembler

DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not configured")
QUOTA_DATE = date(2026, 9, 19)


def _clear() -> None:
    assert DSN
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("delete from l3_execution_job")
        cur.execute("delete from analysis_run")
        cur.execute("delete from research_task_cost")
        cur.execute("delete from research_promotion_decision")
        cur.execute("delete from research_promotion_batch")
        cur.execute("delete from daily_research_quota")
        cur.execute("delete from pipeline_run")
        cur.execute("delete from source_video")
        conn.commit()


def _video(*, level: int = 2, selected: bool = True, key: str = "main") -> UUID:
    assert DSN
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into pipeline_run(run_type, run_version, platform, status)
            values ('synthetic-source', 'test', 'douyin', 'success')
            returning id
            """
        )
        source_run_id = cur.fetchone()[0]
        cur.execute(
            """
            insert into source_video(
              platform, platform_video_id, title, description, published_at,
              duration_ms, availability_status, research_level
            ) values (
              'douyin', %s, '公开标题', '已复核的说明',
              '2026-09-19 11:00:00+08', 120000, 'available', %s
            )
            returning id
            """,
            (f"private-platform-video-{key}", level),
        )
        video_id = cur.fetchone()[0]
        cur.execute(
            """
            insert into human_annotation(
              video_id, actor, annotation_type, value
            ) values (
              %s, 'synthetic-reviewer', 'l3_privacy_review',
              '{"reviewed":true,"version":"privacy-v1"}'::jsonb
            )
            """,
            (video_id,),
        )
        cur.execute(
            """
            insert into pipeline_run_item(
              run_id, entity_type, entity_id, stage, outcome
            ) values (%s, 'video', %s, 'L2', 'scored')
            """,
            (source_run_id, video_id),
        )
        cur.execute(
            """
            insert into video_score(video_id, score_type, score, rule_version)
            values (%s, 'priority', 90, 'synthetic-v1')
            """,
            (video_id,),
        )
        conn.commit()

    if selected:
        gate = L3PromotionGate(DSN)
        gate.configure_daily_quota(
            platform="douyin",
            max_items=1,
            quota_date=QUOTA_DATE,
        )
        gate.promote(source_run_id, top_n=1, quota_date=QUOTA_DATE)
    return video_id


def _feature(
    video_id: UUID,
    *,
    fingerprint: str = "private-comment-fingerprint",
    like_sum: int | None = 5,
    reply_sum: int | None = None,
    calculated_at: str = "2026-09-19 03:00:00+00",
) -> None:
    assert DSN
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into video_comment_feature_snapshot(
              video_id, feature_version, evidence_fingerprint, calculated_at,
              sampled_comment_count, root_comment_count, sampled_reply_count,
              source_observation_count, text_present_count,
              question_text_count, like_known_count, like_sum, like_median,
              reply_known_count, reply_sum, reply_median, mean_text_length,
              eligible_text_count, normalized_unique_text_count,
              duplicate_text_count, duplicate_group_count,
              max_duplicate_group_size, url_text_count, mention_text_count,
              emoji_only_text_count, repeated_char_text_count, short_text_count,
              template_like_text_count, char_bigram_count,
              unique_char_bigram_count, top_char_bigram_count,
              top_char_bigram_share, metadata
            ) values (
              %s, 'comment-features-v1.1.0', %s, %s,
              3, 2, 1, 4, 2, 1, 1, %s, %s,
              0, %s, null, 3.5,
              2, 2, 0, 0, 0, 0, 1, 0, 0, 0, 0, 4, 4, 1, 0.25, %s
            )
            """,
            (
                video_id,
                fingerprint,
                calculated_at,
                like_sum,
                like_sum,
                reply_sum,
                Jsonb(
                    {
                        "population_scope": "collected_sample_only",
                        "private_request_id": "must-not-leak",
                        "raw_comment": "must-not-leak",
                    }
                ),
            ),
        )
        conn.commit()


def _transcript(
    video_id: UUID,
    *,
    text: str = "经过复核的转写文本",
    quality: str = "usable",
    fingerprint: str = "private-source-fingerprint",
    created_at: str = "2026-09-19 03:00:00+00",
) -> None:
    assert DSN
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into transcript(
              video_id, asr_provider, model_id, model_revision,
              engine_version, language, text_content, segments,
              source_provider, source_fingerprint, audio_duration_ms,
              quality_status, metadata, created_at
            ) values (
              %s, 'synthetic-asr', 'synthetic-model', 'revision-1',
              'engine-1', 'zh-CN', %s, %s,
              'private-source-provider', %s, 120000, %s, %s, %s
            )
            """,
            (
                video_id,
                text,
                Jsonb(
                    [
                        {
                            "start_ms": 0,
                            "end_ms": 1000,
                            "text": "private-segment-text",
                        }
                    ]
                ),
                fingerprint,
                quality,
                Jsonb({"private_task_id": "must-not-leak"}),
                created_at,
            ),
        )
        conn.commit()


def _assemble(video_id: UUID, *, version: str = "privacy-v1"):
    assert DSN
    return L3EvidenceAssembler(DSN).assemble(
        video_id,
        privacy_reviewed=True,
        privacy_review_version=version,
    )


def test_bundle_is_stable_minimized_and_preserves_null_vs_zero() -> None:
    assert DSN
    _clear()
    video_id = _video()
    _feature(
        video_id,
        fingerprint="old-private-comment-fingerprint",
        like_sum=None,
        calculated_at="2026-09-19 02:00:00+00",
    )
    _feature(
        video_id,
        fingerprint="new-private-comment-fingerprint",
        like_sum=0,
        reply_sum=None,
        calculated_at="2026-09-19 03:00:00+00",
    )
    _transcript(
        video_id,
        text="旧转写",
        fingerprint="old-private-source-fingerprint",
        created_at="2026-09-19 02:00:00+00",
    )
    _transcript(
        video_id,
        text="新转写",
        fingerprint="new-private-source-fingerprint",
        created_at="2026-09-19 03:00:00+00",
    )

    first = _assemble(video_id)
    replay = _assemble(video_id)

    assert first.input_fingerprint == replay.input_fingerprint
    assert len(first.input_fingerprint) == 64
    assert first.evidence_version == L3_EVIDENCE_VERSION
    assert first.evidence_modalities == ("metadata", "comments", "transcript")
    assert first.evidence_bundle["video_metadata"]["published_at"] == (
        "2026-09-19T03:00:00Z"
    )
    assert first.evidence_bundle["comment_features"]["like_sum"] == 0
    assert first.evidence_bundle["comment_features"]["reply_sum"] is None
    assert first.evidence_bundle["transcript"]["text"] == "新转写"
    assert first.evidence_bundle["transcript"]["segment_count"] == 1

    serialized = repr(first.evidence_bundle)
    for secret in (
        str(video_id),
        "private-platform-video",
        "private-comment-fingerprint",
        "private-source-fingerprint",
        "private-source-provider",
        "must-not-leak",
        "private-segment-text",
    ):
        assert secret not in serialized
    assert "evidence_bundle=" not in repr(first)

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select
              (select count(*) from analysis_run),
              (select count(*) from l3_execution_job),
              (select count(*) from research_task_cost)
            """
        )
        assert cur.fetchone() == (0, 0, 0)


def test_latest_rejected_or_unreviewed_transcript_fails_closed() -> None:
    assert DSN
    for quality in ("rejected", "unreviewed"):
        _clear()
        video_id = _video(key=quality)
        _feature(video_id)
        _transcript(
            video_id,
            text="旧的可用转写",
            created_at="2026-09-19 02:00:00+00",
        )
        _transcript(
            video_id,
            text="最新但不可用的转写",
            quality=quality,
            fingerprint=f"private-source-{quality}",
            created_at="2026-09-19 03:00:00+00",
        )

        with pytest.raises(ValueError, match="quality is not eligible"):
            _assemble(video_id)


def test_no_speech_is_explicit_evidence_and_empty_usable_is_rejected() -> None:
    assert DSN
    _clear()
    video_id = _video(key="no-speech")
    _feature(video_id)
    _transcript(video_id, text="", quality="no_speech")

    bundle = _assemble(video_id)
    assert bundle.evidence_bundle["transcript"]["text"] == ""
    assert bundle.evidence_bundle["transcript"]["quality_status"] == "no_speech"

    _clear()
    video_id = _video(key="empty-usable")
    _feature(video_id)
    _transcript(video_id, text="", quality="usable")
    with pytest.raises(ValueError, match="transcript text is required"):
        _assemble(video_id)


def test_gate_level_and_required_evidence_fail_closed() -> None:
    assert DSN
    _clear()
    level_one = _video(level=1, selected=False, key="level-one")
    with pytest.raises(ValueError, match="research level 2"):
        _assemble(level_one)

    _clear()
    unselected = _video(selected=False, key="unselected")
    with pytest.raises(ValueError, match="not selected"):
        _assemble(unselected)

    _clear()
    missing_features = _video(key="missing-features")
    with pytest.raises(ValueError, match="comment feature snapshot"):
        _assemble(missing_features)

    _clear()
    missing_transcript = _video(key="missing-transcript")
    _feature(missing_transcript)
    with pytest.raises(ValueError, match="transcript evidence"):
        _assemble(missing_transcript)

    _clear()
    missing_review = _video(key="missing-review")
    _feature(missing_review)
    _transcript(missing_review)
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "delete from human_annotation where video_id=%s",
            (missing_review,),
        )
        conn.commit()
    with pytest.raises(ValueError, match="persisted L3 privacy review"):
        _assemble(missing_review)


def test_privacy_review_fails_before_database_access() -> None:
    assembler = L3EvidenceAssembler("postgresql://127.0.0.1:1/not-used")

    with pytest.raises(ValueError, match="completed privacy review"):
        assembler.assemble(
            UUID("00000000-0000-0000-0000-000000000001"),
            privacy_reviewed=False,
            privacy_review_version="privacy-v1",
        )
    with pytest.raises(ValueError, match="version is invalid"):
        assembler.assemble(
            UUID("00000000-0000-0000-0000-000000000001"),
            privacy_reviewed=True,
            privacy_review_version="privacy version with spaces",
        )


def test_fingerprint_binds_review_and_content_and_limits_are_enforced() -> None:
    assert DSN
    _clear()
    video_id = _video(key="fingerprint")
    _feature(video_id)
    _transcript(video_id)

    first = _assemble(video_id, version="privacy-v1")
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into human_annotation(
              video_id, actor, annotation_type, value, created_at
            ) values (
              %s, 'synthetic-reviewer-v2', 'l3_privacy_review',
              '{"reviewed":true,"version":"privacy-v2"}'::jsonb,
              '2026-09-20 00:00:00+00'
            )
            """,
            (video_id,),
        )
        conn.commit()
    reviewed_again = _assemble(video_id, version="privacy-v2")
    assert reviewed_again.input_fingerprint != first.input_fingerprint

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "update source_video set title='变更后的标题' where id=%s",
            (video_id,),
        )
        conn.commit()
    changed = _assemble(video_id, version="privacy-v2")
    assert changed.input_fingerprint != first.input_fingerprint

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "update transcript set text_content=%s where video_id=%s",
            ("字" * 30_001, video_id),
        )
        conn.commit()
    with pytest.raises(ValueError, match="transcript text exceeds"):
        _assemble(video_id, version="privacy-v2")
