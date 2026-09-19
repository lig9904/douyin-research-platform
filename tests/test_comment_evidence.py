from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import psycopg
import pytest

from douyin_research.l0l1 import (
    CommentEvidenceStore,
    CommentIngestContext,
    DailyBudgetGuard,
    L0L1Store,
)
from douyin_research.providers.errors import ProviderBudgetError
from douyin_research.providers.store import MemoryProviderStore
from douyin_research.providers.tikhub_provider import TikHubProvider
from douyin_research.providers.transport import TransportResult
from douyin_research.providers.types import CommentSample


DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not configured")


def _clear() -> None:
    assert DSN
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        for table in (
            "video_comment_observation",
            "video_comment",
            "pipeline_run_item",
            "pipeline_run",
            "source_video",
            "source_account",
            "daily_budget",
        ):
            cur.execute(f"delete from {table}")
        conn.commit()


def _insert_video(platform_video_id: str = "video-private") -> None:
    assert DSN
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into source_video(platform, platform_video_id)
            values ('douyin', %s)
            """,
            (platform_video_id,),
        )
        conn.commit()


def _sample(
    comment_id: str,
    *,
    like_count: int | None,
    reply_count: int | None,
    parent_id: str | None = None,
    raw_ref: str = "external_api_response:1",
) -> CommentSample:
    return CommentSample(
        provider="tikhub",
        platform="douyin",
        source_endpoint=(
            "douyin.app.comment_replies"
            if parent_id is not None
            else "douyin.app.comments"
        ),
        video_platform_id="video-private",
        platform_comment_id=comment_id,
        text=f"text {comment_id}",
        parent_platform_comment_id=parent_id,
        like_count=like_count,
        reply_count=reply_count,
        sample_reason="thread_root" if parent_id else "top",
        observed_at=datetime(2026, 9, 19, 10, 0, tzinfo=timezone.utc),
        raw_ref=raw_ref,
    )


def test_comment_ingest_is_replay_safe_and_preserves_null_vs_zero() -> None:
    assert DSN
    _clear()
    _insert_video()
    run_id = L0L1Store(DSN).create_run(
        "comment_sample",
        "v1",
        platform="douyin",
    )
    store = CommentEvidenceStore(DSN)
    context = CommentIngestContext(
        platform="douyin",
        video_platform_id="video-private",
        provider="tikhub",
        request_fingerprint="request-fingerprint-1",
        run_id=run_id,
    )
    samples = [
        _sample("comment-private-1", like_count=0, reply_count=2),
        _sample("comment-private-2", like_count=None, reply_count=None),
        _sample("comment-private-1", like_count=99, reply_count=99),
    ]

    first = store.ingest(samples, context)
    replay = store.ingest(samples, context)

    assert first.new_comments == 2
    assert first.observations_inserted == 2
    assert first.duplicate_input_items == 1
    assert replay.new_comments == 0
    assert replay.observations_inserted == 0
    assert replay.duplicate_observations == 2

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select platform_comment_id, like_count, reply_count, observation_count
            from video_comment
            order by platform_comment_id
            """
        )
        rows = cur.fetchall()
        assert rows == [
            ("comment-private-1", 0, 2, 1),
            ("comment-private-2", None, None, 1),
        ]
        cur.execute("select count(*) from video_comment_observation")
        assert cur.fetchone()[0] == 2
        cur.execute(
            """
            select count(*)
            from pipeline_run_item
            where run_id=%s and entity_type='comment'
            """,
            (run_id,),
        )
        assert cur.fetchone()[0] == 2


def test_new_fingerprint_adds_observation_and_updates_available_metrics() -> None:
    assert DSN
    _clear()
    _insert_video()
    store = CommentEvidenceStore(DSN)
    base = CommentIngestContext(
        platform="douyin",
        video_platform_id="video-private",
        provider="tikhub",
        request_fingerprint="request-fingerprint-1",
    )
    changed = CommentIngestContext(
        platform="douyin",
        video_platform_id="video-private",
        provider="tikhub",
        request_fingerprint="request-fingerprint-2",
    )

    store.ingest([_sample("comment-private", like_count=0, reply_count=None)], base)
    result = store.ingest(
        [
            _sample(
                "comment-private",
                like_count=5,
                reply_count=3,
                raw_ref="external_api_response:2",
            )
        ],
        changed,
    )

    assert result.new_comments == 0
    assert result.observations_inserted == 1
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select like_count, reply_count, observation_count, raw_ref
            from video_comment
            where platform_comment_id='comment-private'
            """
        )
        assert cur.fetchone() == (5, 3, 2, "external_api_response:2")


def test_reply_parent_and_raw_reference_are_persisted() -> None:
    assert DSN
    _clear()
    _insert_video()
    store = CommentEvidenceStore(DSN)

    store.ingest(
        [
            _sample(
                "reply-private",
                like_count=0,
                reply_count=0,
                parent_id="comment-private-root",
            )
        ],
        CommentIngestContext(
            platform="douyin",
            video_platform_id="video-private",
            provider="tikhub",
            request_fingerprint="reply-fingerprint",
        ),
    )

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select parent_platform_comment_id, sample_reason, raw_ref
            from video_comment
            where platform_comment_id='reply-private'
            """
        )
        assert cur.fetchone() == (
            "comment-private-root",
            "thread_root",
            "external_api_response:1",
        )


class BudgetedCommentTransport:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def call(self, spec, kwargs):
        self.calls.append(kwargs)
        cursor = kwargs["cursor"]
        start = 1 if str(cursor) == "0" else 11
        end = 21 if str(cursor) == "0" else 30
        return TransportResult(
            payload={
                "code": 200,
                "request_id": "private-request",
                "data": {
                    "comments": [
                        {"cid": f"comment-{index}", "text": f"text {index}"}
                        for index in range(start, end)
                    ],
                    "cursor": 20 if str(cursor) == "0" else 40,
                    "has_more": 1,
                },
            },
            http_status=200,
            provider_request_id="private-request",
            mode="fake",
        )


def test_budget_hook_counts_only_uncached_external_comment_pages() -> None:
    assert DSN
    _clear()
    guard = DailyBudgetGuard(DSN)
    guard.configure(
        provider="tikhub",
        budget_key="comments",
        max_requests=2,
        max_cost=0.002,
    )
    transport = BudgetedCommentTransport()
    provider = TikHubProvider(
        transport=transport,
        store=MemoryProviderStore(),
        before_external_call=guard.make_before_external_call(
            provider="tikhub",
            budget_key="comments",
        ),
    )

    provider.fetch_comments("video-private", max_pages=2, max_items=40)
    provider.fetch_comments("video-private", max_pages=2, max_items=40)

    assert len(transport.calls) == 2
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select used_requests, spent_cost
            from daily_budget
            where provider='tikhub' and budget_key='comments'
            """
        )
        used_requests, spent_cost = cur.fetchone()
        assert used_requests == 2
        assert float(spent_cost) == pytest.approx(0.002)

    with pytest.raises(ProviderBudgetError):
        provider.fetch_comments_page("video-private", force_refresh=True)
    assert len(transport.calls) == 2
