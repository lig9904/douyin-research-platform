from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from uuid import UUID

import psycopg
import pytest
from psycopg.types.json import Jsonb

from douyin_research.l2 import L3PromotionGate
from douyin_research.l3 import (
    L3_BUDGET_KEY,
    L3_EVIDENCE_VERSION,
    L3ApprovalRequest,
    L3BudgetPreviewRequest,
    L3CandidateStaleError,
    L3EvidenceAssembler,
    L3IdempotencyConflictError,
    L3ReviewService,
)

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


def _approve(video_id: UUID, *, version: str = "privacy-v1"):
    assert DSN
    candidate = L3EvidenceAssembler(DSN).prepare_for_privacy_review(
        video_id,
        privacy_review_version=version,
    )
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into human_annotation(
              video_id, actor, annotation_type, value
            ) values (%s, 'synthetic-reviewer', 'l3_privacy_review', %s)
            """,
            (
                video_id,
                Jsonb(
                    {
                        "reviewed": True,
                        "version": version,
                        "evidence_fingerprint": candidate.input_fingerprint,
                        "evidence_version": candidate.evidence_version,
                        "evidence_modalities": list(candidate.evidence_modalities),
                        "reviewer_identity_source": "windmill_end_user_email_allowlist_v1",
                    }
                ),
            ),
        )
        conn.commit()
    return candidate


def _review_request(video_id: UUID, manifest, *, key: str, actor: str = "reviewer@example.com"):
    return L3ApprovalRequest(
        video_id=video_id,
        actor=actor,
        idempotency_key=key,
        evidence_fingerprint=manifest.evidence_fingerprint,
        evidence_version=manifest.evidence_version,
        evidence_modalities=manifest.evidence_modalities,
        privacy_review_version=manifest.privacy_review_version,
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

    candidate = _approve(video_id)
    first = _assemble(video_id)
    replay = _assemble(video_id)

    assert candidate.input_fingerprint == first.input_fingerprint
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

    _approve(video_id)
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
    with pytest.raises(ValueError, match="persisted L3 privacy review"):
        _assemble(missing_review)


def test_review_service_rebuilds_candidate_and_is_candidate_idempotent() -> None:
    assert DSN
    _clear()
    video_id = _video(key="review-service")
    _feature(video_id)
    _transcript(video_id, text="SENSITIVE_REVIEW_SERVICE_SENTINEL")
    service = L3ReviewService(DSN)
    manifest = service.prepare(video_id, privacy_review_version="privacy-v1")

    first = service.approve(
        _review_request(video_id, manifest, key="review-service-key-1")
    )
    replay = service.approve(
        _review_request(video_id, manifest, key="review-service-key-1")
    )
    second_key = service.approve(
        _review_request(
            video_id,
            manifest,
            key="review-service-key-2",
            actor="second-reviewer@example.com",
        )
    )

    assert first.idempotent_replay is False
    assert replay.idempotent_replay is True
    assert second_key.idempotent_replay is True
    assert first.annotation_id == replay.annotation_id == second_key.annotation_id
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select count(*), min(value->>'reviewer_identity_source')
            from human_annotation
            where video_id=%s and annotation_type='l3_privacy_review'
            """,
            (video_id,),
        )
        assert cur.fetchone() == (1, "windmill_end_user_email_allowlist_v1")

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "update source_video set title='候选已变化' where id=%s",
            (video_id,),
        )
        conn.commit()
    with pytest.raises(L3CandidateStaleError):
        service.approve(
            _review_request(video_id, manifest, key="review-service-stale-key")
        )
    current = service.prepare(video_id, privacy_review_version="privacy-v1")
    with pytest.raises(L3IdempotencyConflictError):
        service.approve(
            _review_request(video_id, current, key="review-service-key-1")
        )
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "select count(*) from human_annotation where video_id=%s",
            (video_id,),
        )
        assert cur.fetchone()[0] == 1


def test_review_service_serializes_different_keys_for_same_candidate() -> None:
    assert DSN
    _clear()
    video_id = _video(key="review-concurrent")
    _feature(video_id)
    _transcript(video_id)
    service = L3ReviewService(DSN)
    manifest = service.prepare(video_id, privacy_review_version="privacy-v1")

    def approve(key: str):
        return service.approve(_review_request(video_id, manifest, key=key))

    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(approve, ("concurrent-key-1", "concurrent-key-2")))

    assert {receipt.annotation_id for receipt in receipts} == {receipts[0].annotation_id}
    assert sorted(receipt.idempotent_replay for receipt in receipts) == [False, True]
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select count(*) from human_annotation
            where video_id=%s and annotation_type='l3_privacy_review'
            """,
            (video_id,),
        )
        assert cur.fetchone()[0] == 1


def test_budget_preview_uses_database_date_and_never_reserves() -> None:
    assert DSN
    _clear()
    video_id = _video(key="budget-preview")
    _feature(video_id)
    _transcript(video_id)
    service = L3ReviewService(DSN)
    manifest = service.prepare(video_id, privacy_review_version="privacy-v1")
    missing = service.preview_budget(
        L3BudgetPreviewRequest(
            video_id=video_id,
            privacy_review_version=manifest.privacy_review_version,
            evidence_fingerprint=manifest.evidence_fingerprint,
            provider="synthetic-l3-review",
            cost_currency="CNY",
            estimated_llm_cost=0,
        )
    )
    assert missing.status == "approval_missing"

    service.approve(_review_request(video_id, manifest, key="budget-preview-key"))
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("select current_date")
        database_date = cur.fetchone()[0]
        cur.execute("delete from daily_budget")
        cur.execute(
            """
            insert into daily_budget(
              budget_date, provider, budget_key, max_cost, max_requests,
              spent_cost, used_requests, cost_currency
            ) values (%s, 'synthetic-l3-review', %s, 0, 1, 0, 0, 'CNY')
            """,
            (database_date, L3_BUDGET_KEY),
        )
        conn.commit()

    request = L3BudgetPreviewRequest(
        video_id=video_id,
        privacy_review_version=manifest.privacy_review_version,
        evidence_fingerprint=manifest.evidence_fingerprint,
        provider="synthetic-l3-review",
        cost_currency="CNY",
        estimated_llm_cost=Decimal("0"),
    )
    preview = service.preview_budget(request)
    assert preview.status == "budget_capacity_preview_only"
    assert preview.budget_date == database_date
    assert preview.max_cost == Decimal("0")
    assert preview.spent_cost == Decimal("0")
    assert preview.estimated_llm_cost == Decimal("0")

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select used_requests, spent_cost from daily_budget
            where budget_date=%s and provider='synthetic-l3-review' and budget_key=%s
            """,
            (database_date, L3_BUDGET_KEY),
        )
        assert cur.fetchone() == (0, Decimal("0"))
        cur.execute("select count(*) from l3_execution_job")
        assert cur.fetchone()[0] == 0
        cur.execute("select count(*) from research_task_cost")
        assert cur.fetchone()[0] == 0
        cur.execute(
            """
            update daily_budget set max_cost=null, max_requests=null
            where budget_date=%s and provider='synthetic-l3-review' and budget_key=%s
            """,
            (database_date, L3_BUDGET_KEY),
        )
        conn.commit()
    unlimited = service.preview_budget(request)
    assert unlimited.max_cost is None
    assert unlimited.max_requests is None


def test_untrusted_legacy_review_cannot_authorize_l3_assembly() -> None:
    assert DSN
    _clear()
    video_id = _video(key="legacy-review")
    _feature(video_id)
    _transcript(video_id)
    candidate = L3EvidenceAssembler(DSN).prepare_for_privacy_review(
        video_id,
        privacy_review_version="privacy-v1",
    )
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into human_annotation(video_id, actor, annotation_type, value)
            values (%s, 'untrusted-legacy-actor', 'l3_privacy_review', %s)
            """,
            (
                video_id,
                Jsonb(
                    {
                        "reviewed": True,
                        "version": "privacy-v1",
                        "evidence_fingerprint": candidate.input_fingerprint,
                        "evidence_version": candidate.evidence_version,
                        "evidence_modalities": list(candidate.evidence_modalities),
                    }
                ),
            ),
        )
        conn.commit()

    with pytest.raises(ValueError, match="does not match evidence"):
        _assemble(video_id)


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

    _approve(video_id, version="privacy-v1")
    first = _assemble(video_id, version="privacy-v1")
    _approve(video_id, version="privacy-v2")
    reviewed_again = _assemble(video_id, version="privacy-v2")
    assert reviewed_again.input_fingerprint != first.input_fingerprint

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "update source_video set title='变更后的标题' where id=%s",
            (video_id,),
        )
        conn.commit()
    with pytest.raises(ValueError, match="does not match evidence"):
        _assemble(video_id, version="privacy-v2")
    changed_candidate = _approve(video_id, version="privacy-v2")
    changed = _assemble(video_id, version="privacy-v2")
    assert changed.input_fingerprint == changed_candidate.input_fingerprint
    assert changed.input_fingerprint != first.input_fingerprint

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "update transcript set text_content=%s where video_id=%s",
            ("字" * 30_001, video_id),
        )
        conn.commit()
    with pytest.raises(ValueError, match="transcript text exceeds"):
        _assemble(video_id, version="privacy-v2")
