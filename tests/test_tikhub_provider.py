from __future__ import annotations

from typing import Any

from douyin_research.providers.store import MemoryProviderStore
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
