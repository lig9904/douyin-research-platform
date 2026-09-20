#!/usr/bin/env python3
"""TikHub V0 paid batch 4: discovery-page linkage, max ten calls.

This is an opt-in, fail-closed probe.  Its fixed budget is: daily usage,
three public price quotes, two low-fan Billboard pages, two video-search
pages, one App V3 batch detail, and daily usage again.  It never retries.

Raw provider envelopes are intentionally short-lived local evidence only. They
are written beneath the gitignored ``tmp/tikhub-v0-batch4/`` with mode 0600;
stdout contains counts, booleans, field names, and public prices only.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

from v0_batch2 import (  # noqa: E402
    CallBudget,
    ProbeFailure,
    metric_coverage,
    pagination,
    public_price_summary,
    safe_shape,
    supports_keyword,
    usage_delta_summary,
    validate_envelope,
    video_candidates,
)

OUT_DIR = Path("tmp/tikhub-v0-batch4")
API_KEY_ENV = "TIKHUB_API_KEY"
GATE_ENV = "TIKHUB_ENABLE_PAID_BATCH4"
GATE_VALUE = "BATCH4_10"
MAX_CALLS = 10
SDK_VERSION = "2.1.1"

LOW_FAN_ENDPOINT = "/api/v1/douyin/billboard/fetch_hot_total_low_fan_list"
VIDEO_SEARCH_ENDPOINT = "/api/v1/douyin/search/fetch_video_search_v2"
MULTI_VIDEO_ENDPOINT = "/api/v1/douyin/app/v3/fetch_multi_video_v2"


def save_raw(label: str, payload: dict[str, Any]) -> Path:
    """Persist evidence locally without making it readable by other users."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        OUT_DIR.chmod(0o700)
    except OSError:
        pass
    path = OUT_DIR / f"{label}.json"
    # os.open prevents a permissive process umask from broadening the file mode.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    finally:
        # fdopen owns descriptor on success; only close when opening/wrapping failed.
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return path


class Batch4Budget(CallBudget):
    def call(self, label: str, method: Callable[..., Any], **kwargs: Any) -> dict[str, Any]:
        if self.calls >= self.max_calls:
            raise ProbeFailure(f"call budget exhausted before {label}")
        self.calls += 1
        payload = validate_envelope(method(**kwargs), label)
        save_raw(label, payload)
        return payload


def require_environment() -> str:
    if os.getenv(GATE_ENV) != GATE_VALUE:
        raise ProbeFailure(f"{GATE_ENV} must equal {GATE_VALUE}")
    api_key = os.getenv(API_KEY_ENV)
    if not api_key:
        raise ProbeFailure(f"{API_KEY_ENV} is missing")
    return api_key


def require_sdk_version(version: str) -> None:
    if version != SDK_VERSION:
        raise ProbeFailure(f"TikHub SDK must equal {SDK_VERSION}")


def preflight_sdk(client: Any) -> None:
    required = {
        "tikhub_user": {
            "get_user_daily_usage": (),
            "calculate_price": ("endpoint", "request_per_day"),
        },
        "douyin_billboard": {
            "fetch_hot_total_low_fan_list": ("page", "page_size", "date_window", "tags"),
        },
        "douyin_search": {
            "fetch_video_search_v2": (
                "keyword", "cursor", "sort_type", "publish_time", "filter_duration",
                "content_type", "search_id", "backtrace",
            ),
        },
        "douyin_app_v3": {"fetch_multi_video_v2": ("body",)},
    }
    for resource_name, methods in required.items():
        resource = getattr(client, resource_name, None)
        if resource is None:
            raise ProbeFailure(f"SDK missing resource {resource_name}")
        for method_name, parameters in methods.items():
            method = getattr(resource, method_name, None)
            if method is None:
                raise ProbeFailure(f"SDK missing method {resource_name}.{method_name}")
            missing = [name for name in parameters if not supports_keyword(method, name)]
            if missing:
                raise ProbeFailure(
                    f"SDK signature mismatch for {resource_name}.{method_name}: {missing}"
                )


def emit(summary: dict[str, Any]) -> None:
    print("BATCH4_PROGRESS " + json.dumps(summary, ensure_ascii=False, sort_keys=True))


def require_next_page(label: str, cursor: Any, has_more: Any) -> None:
    if cursor in (None, "", 0, "0"):
        raise ProbeFailure(f"{label}: missing cursor for fixed second page")
    if has_more in (False, 0, "0", None):
        raise ProbeFailure(f"{label}: no second page available")


def stable_video_ids(payload: dict[str, Any]) -> set[str]:
    return {item["aweme_id"] for item in video_candidates(payload)}


def page_relation(first: set[str], second: set[str]) -> dict[str, int]:
    """Count linkability without placing provider identifiers in output."""
    return {
        "page1_unique_video_count": len(first),
        "page2_unique_video_count": len(second),
        "cross_page_overlap_count": len(first & second),
        "combined_unique_video_count": len(first | second),
    }


def cursor_changed(first: Any, second: Any) -> bool:
    return first not in (None, "") and second not in (None, "") and str(first) != str(second)


def select_detail_ids(*pages: dict[str, Any]) -> list[str]:
    """Select a bounded, de-duplicated batch without logging any selected ID."""
    selected: list[str] = []
    seen: set[str] = set()
    for page in pages:
        for video in video_candidates(page):
            identifier = video["aweme_id"]
            if identifier not in seen:
                selected.append(identifier)
                seen.add(identifier)
            if len(selected) == 50:
                return selected
    if not selected:
        raise ProbeFailure("discovery pages returned no stable aweme_id for detail batch")
    return selected


def _summary(label: str, payload: dict[str, Any]) -> dict[str, Any]:
    # safe_shape intentionally exposes neither IDs, cursor values, nor names.
    return safe_shape(label, payload)


def run() -> int:
    api_key = require_environment()
    from tikhub import TikHub, __version__

    require_sdk_version(__version__)
    if not supports_keyword(TikHub, "max_retries"):
        raise ProbeFailure("SDK constructor cannot enforce max_retries=0")

    budget = Batch4Budget(max_calls=MAX_CALLS)
    summaries: list[dict[str, Any]] = []
    client = TikHub(api_key=api_key, max_retries=0)
    preflight_sdk(client)

    with client:
        usage_before = budget.call("usage_before", client.tikhub_user.get_user_daily_usage)
        summary = _summary("usage_before", usage_before)
        summaries.append(summary)
        emit(summary)

        for label, endpoint in (
            ("price_low_fan", LOW_FAN_ENDPOINT),
            ("price_video_search", VIDEO_SEARCH_ENDPOINT),
            ("price_multi_video", MULTI_VIDEO_ENDPOINT),
        ):
            price = budget.call(
                label,
                client.tikhub_user.calculate_price,
                endpoint=endpoint,
                request_per_day=100,
            )
            summary = _summary(label, price)
            summary["public_pricing"] = public_price_summary(price)
            summaries.append(summary)
            emit(summary)

        billboard1 = budget.call(
            "billboard_low_fan_page1",
            client.douyin_billboard.fetch_hot_total_low_fan_list,
            page=1,
            page_size=10,
            date_window=24,
            tags=[],
        )
        billboard_cursor1, billboard_more1 = pagination(billboard1)
        require_next_page("billboard_low_fan_page1", billboard_cursor1, billboard_more1)
        billboard_ids1 = stable_video_ids(billboard1)
        summary = _summary("billboard_low_fan_page1", billboard1)
        summary.update({"unique_video_count": len(billboard_ids1), "has_more": billboard_more1})
        summaries.append(summary)
        emit(summary)

        billboard2 = budget.call(
            "billboard_low_fan_page2",
            client.douyin_billboard.fetch_hot_total_low_fan_list,
            page=2,
            page_size=10,
            date_window=24,
            tags=[],
        )
        billboard_cursor2, billboard_more2 = pagination(billboard2)
        billboard_ids2 = stable_video_ids(billboard2)
        summary = _summary("billboard_low_fan_page2", billboard2)
        summary.update(page_relation(billboard_ids1, billboard_ids2))
        summary.update({"cursor_changed": cursor_changed(billboard_cursor1, billboard_cursor2), "has_more": billboard_more2})
        summaries.append(summary)
        emit(summary)

        search1 = budget.call(
            "video_search_page1",
            client.douyin_search.fetch_video_search_v2,
            keyword="秦皇岛旅游",
            cursor=0,
            sort_type="0",
            publish_time="7",
            filter_duration="0-1",
            content_type="1",
            search_id="",
            backtrace="",
        )
        search_cursor1, search_more1 = pagination(search1)
        require_next_page("video_search_page1", search_cursor1, search_more1)
        search_ids1 = stable_video_ids(search1)
        summary = _summary("video_search_page1", search1)
        summary.update({"unique_video_count": len(search_ids1), "has_more": search_more1})
        summaries.append(summary)
        emit(summary)

        search2 = budget.call(
            "video_search_page2",
            client.douyin_search.fetch_video_search_v2,
            keyword="秦皇岛旅游",
            cursor=search_cursor1,
            sort_type="0",
            publish_time="7",
            filter_duration="0-1",
            content_type="1",
            search_id="",
            backtrace="",
        )
        search_cursor2, search_more2 = pagination(search2)
        search_ids2 = stable_video_ids(search2)
        summary = _summary("video_search_page2", search2)
        summary.update(page_relation(search_ids1, search_ids2))
        summary.update({"cursor_changed": cursor_changed(search_cursor1, search_cursor2), "has_more": search_more2})
        summaries.append(summary)
        emit(summary)

        detail_ids = select_detail_ids(billboard1, billboard2, search1, search2)
        detail = budget.call(
            "multi_video_detail",
            client.douyin_app_v3.fetch_multi_video_v2,
            body=detail_ids,
        )
        detail_videos = video_candidates(detail)
        summary = _summary("multi_video_detail", detail)
        summary.update({
            "requested_video_count": len(detail_ids),
            "returned_unique_video_count": len(detail_videos),
            "metric_coverage": metric_coverage(detail_videos),
        })
        summaries.append(summary)
        emit(summary)

        usage_after = budget.call("usage_after", client.tikhub_user.get_user_daily_usage)
        summary = _summary("usage_after", usage_after)
        summary.update(usage_delta_summary(usage_before, usage_after))
        summaries.append(summary)
        emit(summary)

    print(json.dumps({
        "sdk_version": __version__,
        "sdk_retries": 0,
        "call_budget_max": budget.max_calls,
        "calls_completed": budget.calls,
        "raw_files_ephemeral": True,
        "summaries": summaries,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def main() -> int:
    try:
        return run()
    except ProbeFailure as exc:
        print(f"BATCH4 FAILED: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # Deliberately omit provider exception text because it can include request material.
        print(f"BATCH4 FAILED: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
