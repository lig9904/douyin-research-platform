from __future__ import annotations

from datetime import datetime, timezone

import pytest

from douyin_research.providers.endpoints import EndpointSpec, get_endpoint
from douyin_research.providers.fingerprint import request_fingerprint
from douyin_research.providers.normalizer import (
    extract_pagination,
    normalize_comment_samples,
    normalize_video_observations,
    validate_tikhub_envelope,
)
from douyin_research.providers.errors import ProviderPermanentError


def test_fingerprint_is_order_stable() -> None:
    a = request_fingerprint(
        provider="tikhub",
        endpoint_key="x",
        params={"b": 2, "a": 1},
        body={"z": [2, 1]},
    )
    b = request_fingerprint(
        provider="tikhub",
        endpoint_key="x",
        params={"a": 1, "b": 2},
        body={"z": [2, 1]},
    )
    assert a == b


def test_allowlist_rejects_unknown_endpoint() -> None:
    with pytest.raises(KeyError):
        get_endpoint("douyin.write.something")


def test_envelope_requires_business_code_200() -> None:
    with pytest.raises(ProviderPermanentError):
        validate_tikhub_envelope({"code": 500, "message": "upstream failed"})


def test_missing_metrics_remain_none_not_zero() -> None:
    payload = {
        "code": 200,
        "request_id": "r1",
        "data": {
            "aweme_detail": {
                "aweme_id": "123",
                "desc": "hello",
                "duration": 35000,
                "create_time": 1_700_000_000,
                "author": {"sec_uid": "sec-1", "nickname": "n"},
                "statistics": {"digg_count": 8},
            }
        },
    }
    items = normalize_video_observations(
        payload,
        endpoint_key="demo",
        raw_ref="raw:1",
        observed_at=datetime.now(timezone.utc),
    )
    assert len(items) == 1
    metrics = items[0].metrics
    assert metrics is not None
    assert metrics.like_count == 8
    assert metrics.play_count is None
    assert metrics.comment_count is None
    assert metrics.metric_status["play_count"] == "unavailable"


def test_comment_normalizer_preserves_zero_and_deduplicates_ids() -> None:
    observed_at = datetime.now(timezone.utc)
    payload = {
        "code": 200,
        "data": {
            "comments": [
                {
                    "cid": "comment-private-1",
                    "text": "first",
                    "digg_count": 0,
                    "reply_comment_total": 2,
                },
                {
                    "cid": "comment-private-1",
                    "text": "duplicate",
                    "digg_count": 8,
                },
                {
                    "cid": "comment-private-2",
                    "text": "second",
                },
            ]
        },
    }

    comments = normalize_comment_samples(
        payload,
        video_platform_id="video-private",
        endpoint_key="douyin.app.comments",
        observed_at=observed_at,
    )

    assert len(comments) == 2
    assert comments[0].like_count == 0
    assert comments[0].reply_count == 2
    assert comments[1].like_count is None


def test_pagination_can_read_stringified_nested_payload() -> None:
    payload = {
        "code": 200,
        "data": {
            "payload": '{"comments": [], "cursor": 20, "has_more": 1}'
        },
    }

    assert extract_pagination(payload) == {"cursor": 20, "has_more": 1}
