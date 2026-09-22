from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from douyin_research.l0l1.research_briefs import (
    config_snapshot,
    depth_plan,
    discovery_source,
    make_config,
)
from douyin_research.l0l1.runner import DiscoverySource, L0L1Runner
from douyin_research.providers.types import ProviderPage, VideoObservation, VideoRef


NOW = datetime(2026, 9, 22, 4, 0, tzinfo=timezone.utc)


def _video(video_id: str, published_at: datetime | None) -> VideoObservation:
    return VideoObservation(
        video=VideoRef(
            provider="fixture",
            platform="douyin",
            platform_video_id=video_id,
            published_at=published_at,
        ),
        account=None,
        metrics=None,
    )


@pytest.mark.parametrize(
    ("source_type", "target", "kind", "argument"),
    [
        ("low_fan", None, "low_fan", "date_window"),
        ("keyword", "文旅 神话", "search", "query"),
        ("account", "MS4wLjABAAAA", "account_posts", "account_id"),
    ],
)
def test_brief_maps_to_one_bounded_source(
    source_type: str, target: str | None, kind: str, argument: str,
) -> None:
    config = make_config(
        source_type=source_type,
        target=target,
        time_window_hours=72,
        max_items=5,
        depth="comments",
        cadence_hours=12,
    )
    source = discovery_source(config, now=NOW)
    assert source.kind == kind
    assert source.max_items == 5
    assert source.published_after == NOW - timedelta(hours=72)
    assert source.kwargs[argument] == (72 if source_type == "low_fan" else target)
    assert target is None or target not in source.source_key
    assert source.kwargs.get("force_refresh") is False


@pytest.mark.parametrize(
    ("time_window_hours", "publish_time"),
    [(24, "1"), (72, "7"), (168, "7"), (720, "180")],
)
def test_keyword_search_uses_latest_provider_window_and_exact_local_cutoff(
    time_window_hours: int, publish_time: str,
) -> None:
    config = make_config(
        source_type="keyword", target="文旅", time_window_hours=time_window_hours,
        max_items=1, depth="metadata", cadence_hours=None,
    )
    source = discovery_source(config, now=NOW)
    assert source.kwargs["sort_type"] == "2"
    assert source.kwargs["publish_time"] == publish_time
    assert source.published_after == NOW - timedelta(hours=time_window_hours)


def test_brief_validation_keeps_paid_scope_bounded() -> None:
    with pytest.raises(ValueError, match="bounded endpoint"):
        make_config(
            source_type="low_fan", target=None, time_window_hours=720,
            max_items=5, depth="metadata", cadence_hours=None,
        )
    with pytest.raises(ValueError, match="bounded endpoint"):
        make_config(
            source_type="low_fan", target=None, time_window_hours=24,
            max_items=6, depth="metadata", cadence_hours=None,
        )
    with pytest.raises(ValueError, match="target is invalid"):
        make_config(
            source_type="keyword", target="", time_window_hours=24,
            max_items=5, depth="metadata", cadence_hours=None,
        )
    with pytest.raises(ValueError, match="cadence_hours"):
        make_config(
            source_type="account", target="account", time_window_hours=24,
            max_items=5, depth="metadata", cadence_hours=1,
        )


def test_depth_only_routes_collection_and_never_auto_submits_analysis() -> None:
    assert depth_plan("metadata") == {
        "collect_comments": False,
        "collect_media": False,
        "review_required": False,
        "auto_submit_asr": False,
        "auto_submit_l3": False,
    }
    assert depth_plan("review_ready") == {
        "collect_comments": True,
        "collect_media": True,
        "review_required": True,
        "auto_submit_asr": False,
        "auto_submit_l3": False,
    }


def test_snapshot_is_exact_normalized_configuration() -> None:
    config = make_config(
        source_type="keyword", target="  神话   文旅 ", time_window_hours=168,
        max_items=5, depth="media", cadence_hours=24,
    )
    assert config.target == "神话 文旅"
    assert config_snapshot(config) == {
        "platform": "douyin",
        "source_type": "keyword",
        "target": "神话 文旅",
        "time_window_hours": 168,
        "max_items": 5,
        "depth": "media",
        "cadence_hours": 24,
    }


class _AccountProvider:
    provider_name = "fixture"
    platform_name = "douyin"
    video_batch_size = 50
    capabilities = frozenset({"account.posts"})

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def fetch_account_posts(self, account_id: str, **kwargs):
        self.calls.append((account_id, kwargs))
        return ProviderPage(
            items=[], endpoint_key="fixture.account", request_fingerprint="fixture",
            cached=True,
        )


def test_runner_dispatches_account_posts_without_generic_passthrough() -> None:
    provider = _AccountProvider()
    runner = object.__new__(L0L1Runner)
    runner.provider = provider
    page = runner._fetch(
        DiscoverySource(
            kind="account_posts", source_type="brief_account", source_key="opaque",
            kwargs={"account_id": "sec-user", "count": 10}, max_items=10,
        )
    )
    assert page.cached is True
    assert provider.calls == [("sec-user", {"count": 10})]


def test_runner_applies_strict_local_publication_cutoff() -> None:
    items = [
        _video("old", NOW - timedelta(hours=25)),
        _video("edge", NOW - timedelta(hours=24)),
        _video("new", NOW - timedelta(hours=1)),
        _video("unknown", None),
    ]
    assert [
        item.video.platform_video_id
        for item in L0L1Runner._within_published_window(
            items, published_after=NOW - timedelta(hours=24),
        )
    ] == ["edge", "new"]
