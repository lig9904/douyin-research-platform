from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest

from douyin_research.providers.store import MemoryProviderStore
from douyin_research.providers.errors import ProviderPermanentError
from douyin_research.providers.errors import ProviderSchemaError
from douyin_research.providers.tikhub_provider import TikHubProvider
from douyin_research.providers.transport import TransportResult


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call(self, spec, kwargs):
        self.calls.append((spec.key, kwargs))
        if spec.key == "douyin.app.multi_video_v2":
            ids = kwargs["body"]
            return TransportResult(
                payload={
                    "code": 200,
                    "request_id": f"r{len(self.calls)}",
                    "data": [
                        {
                            "aweme_id": video_id,
                            "duration": 1000,
                            "author": {"sec_uid": f"u-{video_id}"},
                            "statistics": {},
                        }
                        for video_id in ids
                    ],
                },
                http_status=200,
                provider_request_id=f"r{len(self.calls)}",
                mode="fake",
            )

        return TransportResult(
            payload={
                "code": 200,
                "request_id": f"r{len(self.calls)}",
                "data": {
                    "aweme_list": [
                        {
                            "aweme_id": "v1",
                            "desc": "test",
                            "author": {"sec_uid": "u1"},
                            "statistics": {"digg_count": 10},
                        }
                    ]
                },
            },
            http_status=200,
            provider_request_id=f"r{len(self.calls)}",
            mode="fake",
        )


def test_persistent_cache_avoids_second_external_call() -> None:
    transport = FakeTransport()
    store = MemoryProviderStore()
    provider = TikHubProvider(transport=transport, store=store)

    first = provider.fetch_low_fan_billboard(page=1, page_size=5, date_window=24)
    second = provider.fetch_low_fan_billboard(page=1, page_size=5, date_window=24)

    assert len(transport.calls) == 1
    assert first.cached is False
    assert second.cached is True
    assert len(store.calls) == 2
    assert store.calls[1].cached is True
    assert store.calls[1].actual_cost == 0.0
    assert store.calls[0].actual_cost is None
    assert store.calls[0].estimated_cost == pytest.approx(0.001)
    assert store.calls[0].metadata["cost_basis"] == "estimated_unit_price"
    assert store.calls[0].metadata["pricing_version"] == "public-tariff-2026-09-20"
    assert store.calls[1].metadata["cost_basis"] == "cache_zero"


def test_account_profile_is_identity_bound_cached_and_estimated_not_billed() -> None:
    class ProfileTransport:
        def __init__(self) -> None:
            self.calls = []

        def call(self, spec, kwargs):
            self.calls.append((spec, kwargs))
            return TransportResult(
                payload={"code": 200, "data": {"user": {
                    "sec_uid": "sec-stable", "nickname": "角色号", "follower_count": 1755,
                }}}, http_status=200, provider_request_id="profile-1", mode="fake",
            )

    transport = ProfileTransport()
    store = MemoryProviderStore()
    provider = TikHubProvider(transport=transport, store=store)
    first = provider.fetch_account_profile("sec-stable")
    second = provider.fetch_account_profile("sec-stable")
    assert len(transport.calls) == 1
    spec, kwargs = transport.calls[0]
    assert spec.path == "/api/v1/douyin/app/v3/handler_user_profile"
    assert spec.http_method == "GET" and kwargs == {"sec_user_id": "sec-stable"}
    assert first.items[0].platform_account_id == "sec-stable"
    assert first.items[0].follower_count == 1755
    assert first.items[0].raw_ref == "memory:1"
    assert second.cached is True and second.items[0].follower_count == 1755
    assert store.calls[0].estimated_cost == pytest.approx(0.001)
    assert store.calls[0].actual_cost is None
    assert store.calls[1].estimated_cost == store.calls[1].actual_cost == 0


def test_account_profile_rejects_wrong_identity_before_cache_or_snapshot() -> None:
    class WrongProfileTransport:
        def call(self, spec, kwargs):
            return TransportResult(
                payload={"code": 200, "data": {"user": {
                    "sec_uid": "someone-else", "follower_count": 9000,
                }}}, http_status=200, provider_request_id="wrong-profile", mode="fake",
            )

    store = MemoryProviderStore()
    provider = TikHubProvider(transport=WrongProfileTransport(), store=store)
    with pytest.raises(ProviderSchemaError, match="does not match"):
        provider.fetch_account_profile("sec-stable")
    assert store.responses == []
    assert len(store.calls) == 1 and store.calls[0].status == "error"
    with pytest.raises(ValueError, match="stable nonempty"):
        provider.fetch_account_profile(" ")
    assert len(store.calls) == 1


def test_batch_detail_quote_is_not_reconciled_spend_and_cache_is_free():
    store = MemoryProviderStore()
    transport = FakeTransport()
    provider = TikHubProvider(transport=transport, store=store)
    provider.fetch_videos(["v1", "v2", "v3"])
    provider.fetch_videos(["v1", "v2", "v3"])
    assert len(transport.calls) == 1
    assert store.calls[0].actual_cost is None
    assert store.calls[0].estimated_cost == pytest.approx(0.05)
    assert store.calls[0].metadata["price_source"] == "tikhub.get_all_endpoints_info"
    assert store.calls[1].actual_cost == 0


def test_uncached_transport_guard_receives_exact_detail_batches_and_wraps_call() -> None:
    transport = FakeTransport()
    events: list[tuple[str, object]] = []

    @contextmanager
    def guard(spec, video_ids):
        events.append(("enter", (spec.key, video_ids)))
        try:
            yield
        finally:
            events.append(("exit", (spec.key, video_ids)))

    provider = TikHubProvider(
        transport=transport,
        store=MemoryProviderStore(),
        uncached_transport_guard=guard,
        before_external_call=lambda spec: events.append(("budget", spec.key)),
    )
    ids = [str(index) for index in range(55)]

    provider.fetch_videos(ids)
    provider.fetch_videos(ids)

    first_batch = tuple(ids[:50])
    second_batch = tuple(ids[50:])
    assert events == [
        ("enter", ("douyin.app.multi_video_v2", first_batch)),
        ("budget", "douyin.app.multi_video_v2"),
        ("exit", ("douyin.app.multi_video_v2", first_batch)),
        ("enter", ("douyin.app.multi_video_v2", second_batch)),
        ("budget", "douyin.app.multi_video_v2"),
        ("exit", ("douyin.app.multi_video_v2", second_batch)),
    ]
    assert [call[1]["body"] for call in transport.calls] == [
        list(first_batch), list(second_batch),
    ]


def test_uncached_transport_guard_can_refuse_before_budget_or_http_attempt() -> None:
    transport = FakeTransport()
    store = MemoryProviderStore()
    budget_calls: list[str] = []

    @contextmanager
    def refusing_guard(spec, video_ids):
        assert spec.key == "douyin.app.multi_video_v2"
        assert video_ids == ("only-id",)
        raise PermissionError("project batch is no longer eligible")
        yield

    provider = TikHubProvider(
        transport=transport,
        store=store,
        uncached_transport_guard=refusing_guard,
        before_external_call=lambda spec: budget_calls.append(spec.key),
    )

    with pytest.raises(PermissionError, match="no longer eligible"):
        provider.fetch_videos(["only-id"])

    assert budget_calls == []
    assert transport.calls == []
    assert store.calls == []


def test_low_fan_compact_billboard_schema_is_normalized() -> None:
    class CompactLowFanTransport:
        def call(self, spec, kwargs):
            return TransportResult(
                payload={
                    "code": 200,
                    "data": {
                        "data": {
                            "objs": [
                                {
                                    "item_id": "7420000000000000001",
                                    "item_title": "公开标题",
                                    "item_url": "https://www.douyin.com/video/7420000000000000001",
                                    "item_duration": 12345,
                                    "publish_time": 1_700_000_000,
                                    "play_cnt": 321,
                                    "like_cnt": 45,
                                    "fans_cnt": 67,
                                    "nick_name": "不作为稳定账号标识",
                                    "favorite_id": 999,
                                }
                            ]
                        }
                    },
                },
                http_status=200,
                provider_request_id="compact-low-fan",
                mode="fake",
            )

    provider = TikHubProvider(
        transport=CompactLowFanTransport(),
        store=MemoryProviderStore(),
    )

    page = provider.fetch_low_fan_billboard(page_size=1)

    assert len(page.items) == 1
    item = page.items[0]
    assert item.video.platform_video_id == "7420000000000000001"
    assert item.video.source_url == "https://www.douyin.com/video/7420000000000000001"
    assert item.video.account_platform_id is None
    assert item.account is None
    assert item.metrics.play_count == 321
    assert item.metrics.like_count == 45
    assert item.metrics.author_follower_count == 67


def test_fetch_videos_chunks_at_50_and_deduplicates_ids() -> None:
    transport = FakeTransport()
    store = MemoryProviderStore()
    provider = TikHubProvider(transport=transport, store=store)

    ids = [str(i) for i in range(105)] + ["1", "2"]
    items = provider.fetch_videos(ids)

    assert len(items) == 105
    batch_calls = [x for x in transport.calls if x[0] == "douyin.app.multi_video_v2"]
    assert [len(x[1]["body"]) for x in batch_calls] == [50, 50, 5]


def test_force_refresh_bypasses_cache() -> None:
    transport = FakeTransport()
    store = MemoryProviderStore()
    provider = TikHubProvider(transport=transport, store=store)

    provider.fetch_low_fan_billboard()
    provider.fetch_low_fan_billboard(force_refresh=True)

    assert len(transport.calls) == 2


def test_failed_call_persists_only_safe_provider_failure_metadata() -> None:
    class FailingTransport:
        def call(self, _spec, _kwargs):
            error = ProviderPermanentError("raw body video=secret url=https://private.invalid")
            error.provider_diagnostic = {
                "http_status": 400,
                "provider_error_code": "INVALID_PARAMETER",
                "provider_request_id": "req-safe-400",
            }
            raise error

    store = MemoryProviderStore()
    provider = TikHubProvider(transport=FailingTransport(), store=store)

    with pytest.raises(ProviderPermanentError):
        provider.fetch_videos(["sensitive-video-id"])

    failure = store.calls[0].metadata["failure"]
    assert failure["status"] == "failed"
    assert failure["http_status"] == 400
    assert failure["provider_error_code"] == "INVALID_PARAMETER"
    assert failure["provider_request_id"] == "req-safe-400"
    assert failure["ledger_logical_call_id"] == store.calls[0].metadata["logical_call_id"]
    assert "secret" not in str(store.calls[0].metadata)
    assert "private.invalid" not in str(store.calls[0].metadata)


class FakeCommentTransport:
    def __init__(self, *, stalled_cursor: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.stalled_cursor = stalled_cursor

    def call(self, spec, kwargs):
        self.calls.append((spec.key, kwargs))
        if spec.key == "douyin.app.comment_replies":
            return TransportResult(
                payload={
                    "code": 200,
                    "request_id": "private-reply-request",
                    "data": {
                        "comments": [
                            {
                                "cid": f"reply-{index}",
                                "text": f"reply {index}",
                                "digg_count": 0,
                            }
                            for index in range(15)
                        ],
                        "cursor": 15,
                        "has_more": 0,
                    },
                },
                http_status=200,
                provider_request_id="private-reply-request",
                mode="fake",
            )

        cursor = kwargs["cursor"]
        if str(cursor) == "0":
            ids = [f"comment-{index}" for index in range(1, 21)]
            next_cursor = 0 if self.stalled_cursor else 20
        else:
            ids = [f"comment-{index}" for index in range(11, 30)]
            next_cursor = 40
        return TransportResult(
            payload={
                "code": 200,
                "request_id": "private-comment-request",
                "data": {
                    "comments": [
                        {
                            "cid": comment_id,
                            "text": f"text {comment_id}",
                            "digg_count": 0,
                            "reply_comment_total": 1 if comment_id == "comment-1" else 0,
                        }
                        for comment_id in ids
                    ],
                    "cursor": next_cursor,
                    "has_more": 1,
                },
            },
            http_status=200,
            provider_request_id="private-comment-request",
            mode="fake",
        )


def test_comments_are_bounded_and_deduplicated_across_pages() -> None:
    transport = FakeCommentTransport()
    guarded: list[str] = []
    provider = TikHubProvider(
        transport=transport,
        store=MemoryProviderStore(),
        before_external_call=lambda spec: guarded.append(spec.key),
    )

    page = provider.fetch_comments(
        "video-private",
        max_pages=2,
        max_items=40,
    )

    assert len(page.items) == 29
    assert len({item.platform_comment_id for item in page.items}) == 29
    assert page.pagination["pages_fetched"] == 2
    assert page.pagination["duplicates_removed"] == 10
    assert len(transport.calls) == 2
    assert guarded == ["douyin.app.comments", "douyin.app.comments"]



def test_duplicate_count_excludes_unprocessed_items_after_max_items() -> None:
    provider = TikHubProvider(
        transport=FakeCommentTransport(),
        store=MemoryProviderStore(),
    )

    page = provider.fetch_comments(
        "video-private",
        max_pages=2,
        max_items=25,
    )

    assert len(page.items) == 25
    assert page.pagination["duplicates_removed"] == 10



def test_comment_pagination_stops_on_unchanged_cursor() -> None:
    transport = FakeCommentTransport(stalled_cursor=True)
    provider = TikHubProvider(
        transport=transport,
        store=MemoryProviderStore(),
    )

    page = provider.fetch_comments(
        "video-private",
        max_pages=5,
        max_items=100,
    )

    assert page.pagination["pages_fetched"] == 1
    assert len(transport.calls) == 1


def test_cached_comment_page_does_not_consume_external_call_guard() -> None:
    transport = FakeCommentTransport()
    guarded: list[str] = []
    provider = TikHubProvider(
        transport=transport,
        store=MemoryProviderStore(),
        before_external_call=lambda spec: guarded.append(spec.key),
    )

    first = provider.fetch_comments_page("video-private")
    second = provider.fetch_comments_page("video-private")

    assert first.cached is False
    assert second.cached is True
    assert first.raw_ref == second.raw_ref
    assert len(transport.calls) == 1
    assert guarded == ["douyin.app.comments"]


def test_comment_replies_are_provider_neutral() -> None:
    transport = FakeCommentTransport()
    provider = TikHubProvider(
        transport=transport,
        store=MemoryProviderStore(),
    )

    page = provider.fetch_comment_replies(
        "video-private",
        "comment-private",
    )

    assert len(page.items) == 15
    assert page.pagination["has_more"] == 0
    assert {item.sample_reason for item in page.items} == {"thread_root"}
    assert all(item.video_platform_id == "video-private" for item in page.items)


def test_comment_limits_are_hard_bounds() -> None:
    provider = TikHubProvider(
        transport=FakeCommentTransport(),
        store=MemoryProviderStore(),
    )

    with pytest.raises(ValueError, match="max_pages"):
        provider.fetch_comments("video-private", max_pages=0)
    with pytest.raises(ValueError, match="max_items"):
        provider.fetch_comments("video-private", max_items=0)
    with pytest.raises(ValueError, match="between 1 and 20"):
        provider.fetch_comments_page("video-private", count=21)
