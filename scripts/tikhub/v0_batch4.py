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
import stat
import sys
from pathlib import Path
from typing import Any, Callable

from v0_batch2 import (  # noqa: E402
    CallBudget,
    ProbeFailure,
    first_value,
    metric_coverage,
    pagination,
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
PRICE_TOKENS = ("price", "cost", "discount", "request")
SENSITIVE_PRICE_TOKENS = ("id", "uid", "token", "cursor", "key", "secret")


def _open_raw_dir(*, create: bool) -> int | None:
    """Open the exact output directory without following a replacement symlink."""
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    nofollow_flag = getattr(os, "O_NOFOLLOW", 0)
    if not directory_flag or not nofollow_flag:
        raise ProbeFailure("platform cannot enforce safe raw directory access")
    if create:
        OUT_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        descriptor = os.open(OUT_DIR, os.O_RDONLY | directory_flag | nofollow_flag)
    except FileNotFoundError:
        if not create:
            return None
        raise ProbeFailure("raw output directory disappeared before opening")
    except OSError as exc:
        raise ProbeFailure("raw output directory could not be opened safely") from exc
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ProbeFailure("raw output path is not a directory")
    try:
        os.fchmod(descriptor, 0o700)
    except OSError as exc:
        os.close(descriptor)
        raise ProbeFailure("raw output directory permissions could not be secured") from exc
    return descriptor


def save_raw(label: str, payload: dict[str, Any]) -> Path:
    """Persist evidence locally without making it readable by other users."""
    if not label or Path(label).name != label:
        raise ProbeFailure("raw evidence label must be a plain file name")
    directory_descriptor = _open_raw_dir(create=True)
    assert directory_descriptor is not None
    file_name = f"{label}.json"
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
    try:
        try:
            descriptor = os.open(file_name, flags, 0o600, dir_fd=directory_descriptor)
        except OSError as exc:
            raise ProbeFailure("raw evidence path could not be opened safely") from exc
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            os.chmod(file_name, 0o600, dir_fd=directory_descriptor, follow_symlinks=False)
        except OSError as exc:
            raise ProbeFailure("raw evidence could not be written safely") from exc
    finally:
        os.close(directory_descriptor)
    return OUT_DIR / file_name


def cleanup_raw() -> None:
    """Delete direct regular JSON evidence via a non-following directory handle."""
    directory_descriptor = _open_raw_dir(create=False)
    if directory_descriptor is None:
        return
    try:
        for name in os.listdir(directory_descriptor):
            if not name.endswith(".json") or Path(name).name != name:
                continue
            try:
                metadata = os.stat(
                    name,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                continue
            if stat.S_ISREG(metadata.st_mode):
                os.unlink(name, dir_fd=directory_descriptor)
    finally:
        os.close(directory_descriptor)
    try:
        OUT_DIR.rmdir()
    except OSError:
        pass


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


def require_search_next_page(
    label: str, cursor: Any, search_id: Any, backtrace: Any, has_more: Any
) -> None:
    if cursor in (None, "", 0, "0"):
        raise ProbeFailure(f"{label}: missing cursor for second page")
    if not isinstance(search_id, str) or not search_id:
        raise ProbeFailure(f"{label}: missing search_id for second page")
    if not isinstance(backtrace, str) or not backtrace:
        raise ProbeFailure(f"{label}: missing backtrace for second page")
    if has_more not in (True, 1, "1"):
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


def require_billboard_second_page(has_more: Any) -> None:
    """Billboard paginates by fixed page number, not cursor tokens."""
    if has_more in (False, 0, "0"):
        raise ProbeFailure("billboard_low_fan_page1: no second page available")


def select_source_ids(pages: tuple[dict[str, Any], ...], seen: set[str]) -> list[str]:
    """Take at most ten source-local IDs, excluding IDs allocated to another source."""
    selected: list[str] = []
    for page in pages:
        for video in video_candidates(page):
            identifier = video["aweme_id"]
            if identifier in seen:
                continue
            selected.append(identifier)
            seen.add(identifier)
            if len(selected) == 10:
                return selected
    return selected


def select_detail_sources(
    billboard_pages: tuple[dict[str, Any], ...], search_pages: tuple[dict[str, Any], ...]
) -> dict[str, list[str]]:
    """Build a max-20 batch with an independently bounded contribution per source."""
    seen: set[str] = set()
    selected = {
        "billboard": select_source_ids(billboard_pages, seen),
        "search": select_source_ids(search_pages, seen),
    }
    if not selected["billboard"] or not selected["search"]:
        raise ProbeFailure("detail batch requires at least one Billboard and one Search video")
    return selected


def detail_source_summary(selected: dict[str, list[str]], payload: dict[str, Any]) -> dict[str, int]:
    """Report source-local result counts only; missing results have no status meaning."""
    returned = stable_video_ids(payload)
    summary: dict[str, int] = {}
    for source, identifiers in selected.items():
        requested = set(identifiers)
        returned_count = len(requested & returned)
        summary[f"{source}_requested_count"] = len(requested)
        summary[f"{source}_returned_count"] = returned_count
        summary[f"{source}_not_returned_count"] = len(requested) - returned_count
    return summary


def search_page_tokens(payload: dict[str, Any]) -> tuple[Any, Any, Any, Any]:
    data = payload.get("data")
    return (
        first_value(data, ("cursor",)),
        first_value(data, ("search_id",)),
        first_value(data, ("backtrace",)),
        first_value(data, ("has_more",)),
    )


def public_price_summary(payload: dict[str, Any]) -> dict[str, float]:
    """Keep public pricing leaves while excluding identifiers and opaque request metadata."""
    result: dict[str, float] = {}

    def visit(value: Any, path: tuple[str, ...]) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                visit(child, (*path, str(key)))
            return
        if isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, (*path, str(index)))
            return
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return
        normalized = ".".join(path).lower()
        if any(token in normalized for token in SENSITIVE_PRICE_TOKENS):
            return
        if any(token in normalized for token in PRICE_TOKENS):
            result[".".join(path)] = float(value)

    visit(payload.get("data"), ("data",))
    return result


def _summary(label: str, payload: dict[str, Any]) -> dict[str, Any]:
    # safe_shape intentionally exposes neither IDs, cursor values, nor names.
    return safe_shape(label, payload)


def _run(api_key: str) -> int:
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
        billboard_ids1 = stable_video_ids(billboard1)
        if not billboard_ids1:
            raise ProbeFailure("billboard_low_fan_page1: no stable aweme_id")
        _, billboard_more1 = pagination(billboard1)
        require_billboard_second_page(billboard_more1)
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
        _, billboard_more2 = pagination(billboard2)
        billboard_ids2 = stable_video_ids(billboard2)
        summary = _summary("billboard_low_fan_page2", billboard2)
        summary.update(page_relation(billboard_ids1, billboard_ids2))
        summary.update({"has_more": billboard_more2})
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
        search_ids1 = stable_video_ids(search1)
        if not search_ids1:
            raise ProbeFailure("video_search_page1: no stable aweme_id")
        search_cursor1, search_id1, backtrace1, search_more1 = search_page_tokens(search1)
        require_search_next_page(
            "video_search_page1", search_cursor1, search_id1, backtrace1, search_more1
        )
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
            search_id=search_id1,
            backtrace=backtrace1,
        )
        search_cursor2, _, _, search_more2 = search_page_tokens(search2)
        search_ids2 = stable_video_ids(search2)
        summary = _summary("video_search_page2", search2)
        summary.update(page_relation(search_ids1, search_ids2))
        summary.update({"cursor_changed": cursor_changed(search_cursor1, search_cursor2), "has_more": search_more2})
        summaries.append(summary)
        emit(summary)

        detail_sources = select_detail_sources(
            (billboard1, billboard2), (search1, search2)
        )
        detail_ids = detail_sources["billboard"] + detail_sources["search"]
        detail = budget.call(
            "multi_video_detail",
            client.douyin_app_v3.fetch_multi_video_v2,
            body=detail_ids,
        )
        detail_videos = video_candidates(detail)
        summary = _summary("multi_video_detail", detail)
        summary.update(detail_source_summary(detail_sources, detail))
        summary.update({"returned_unique_video_count": len(detail_videos), "metric_coverage": metric_coverage(detail_videos)})
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


def run() -> int:
    api_key = require_environment()
    cleanup_raw()
    try:
        return _run(api_key)
    finally:
        cleanup_raw()


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
