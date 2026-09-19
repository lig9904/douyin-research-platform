#!/usr/bin/env python3
"""TikHub V0 smoke test.

Default behavior runs all four FREE demo endpoints and does not require TIKHUB_API_KEY:
    python scripts/tikhub/v0_smoke.py demo

Paid smoke calls are opt-in and hard-gated:
    export TIKHUB_API_KEY="..."
    export TIKHUB_ENABLE_PAID_SMOKE=YES
    python scripts/tikhub/v0_smoke.py billboard
    python scripts/tikhub/v0_smoke.py creator
    python scripts/tikhub/v0_smoke.py search
    python scripts/tikhub/v0_smoke.py appv3 --aweme-id <id>
    python scripts/tikhub/v0_smoke.py all-paid --aweme-id <id>

Raw responses are written under tmp/tikhub-v0/ (gitignored).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

DEMO_URLS = {
    "demo_douyin_app_video": "https://api.tikhub.io/api/v1/demo/douyin/app/fetch_one_video",
    "demo_douyin_web_video": "https://api.tikhub.io/api/v1/demo/douyin/web/fetch_one_video",
    "demo_douyin_search": "https://api.tikhub.io/api/v1/demo/douyin_search/app/general_search",
    "demo_cache_status": "https://api.tikhub.io/api/v1/demo/demo/cache_status",
}
OUT_DIR = Path("tmp/tikhub-v0")
PAID_GATE = "TIKHUB_ENABLE_PAID_SMOKE"
API_KEY_ENV = "TIKHUB_API_KEY"
SDK_MAX_RETRIES = 0


class SmokeFailure(RuntimeError):
    pass


def now_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def validate_envelope(resp: Any, label: str) -> dict[str, Any]:
    if not isinstance(resp, dict):
        raise SmokeFailure(f"{label}: expected dict response, got {type(resp)!r}")
    if resp.get("code") != 200:
        raise SmokeFailure(
            f"{label}: TikHub business code={resp.get('code')!r}, "
            f"message={resp.get('message')!r}, request_id={resp.get('request_id')!r}"
        )
    return resp


def save_raw(label: str, resp: dict[str, Any]) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{now_slug()}_{label}.json"
    path.write_text(json.dumps(resp, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def dig(obj: Any, *path: str) -> Any:
    cur = obj
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def first_list(obj: Any) -> list[Any] | None:
    """Find the first reasonably-sized list below a response for shape inspection."""
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for value in obj.values():
            found = first_list(value)
            if found is not None:
                return found
    return None


def summarize_demo(
    label: str,
    payload: dict[str, Any],
    *,
    http_status: int,
    raw_path: Path,
) -> dict[str, Any]:
    """Return a small, non-sensitive shape summary for one free demo response."""
    summary: dict[str, Any] = {
        "label": label,
        "http_status": http_status,
        "tikhub_code": payload.get("code"),
        "request_id_present": bool(payload.get("request_id")),
        "router": payload.get("router"),
        "raw_path": str(raw_path),
    }

    if label in {"demo_douyin_app_video", "demo_douyin_web_video"}:
        aweme = dig(payload, "data", "aweme_detail") or {}
        author = aweme.get("author") if isinstance(aweme, dict) else {}
        video = aweme.get("video") if isinstance(aweme, dict) else {}
        statistics = aweme.get("statistics") if isinstance(aweme, dict) else {}
        summary.update(
            {
                "aweme_id": aweme.get("aweme_id"),
                "author_sec_uid_present": bool(author.get("sec_uid"))
                if isinstance(author, dict)
                else False,
                "duration_present": aweme.get("duration") is not None,
                "play_count_present": statistics.get("play_count") is not None
                if isinstance(statistics, dict)
                else False,
                "video_field_present": isinstance(video, dict),
            }
        )
        required = ["aweme_id", "author_sec_uid_present", "duration_present"]
    elif label == "demo_douyin_search":
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        cards = data.get("data") if isinstance(data.get("data"), list) else []
        aweme_ids = {
            str(obj["aweme_id"])
            for card in cards
            for obj in _walk_dicts(card)
            if obj.get("aweme_id")
        }
        summary.update(
            {
                "card_count": len(cards),
                "unique_aweme_id_count": len(aweme_ids),
                "has_more": data.get("has_more"),
                "cursor": data.get("cursor"),
                "backtrace_present": bool(data.get("backtrace")),
            }
        )
        required = ["card_count", "unique_aweme_id_count", "cursor"]
    else:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        cache_items = data.get("cache_items") if isinstance(data.get("cache_items"), list) else []
        summary.update(
            {
                "total_cached_items": data.get("total_cached_items"),
                "cache_item_count": len(cache_items),
            }
        )
        required = ["total_cached_items"]

    missing = [
        field
        for field in required
        if summary.get(field) is None
        or summary.get(field) is False
        or summary.get(field) == ""
    ]
    if label == "demo_douyin_search":
        missing.extend(
            field
            for field in ("card_count", "unique_aweme_id_count")
            if summary.get(field) == 0 and field not in missing
        )
    if missing:
        raise SmokeFailure(f"{label} missing required demo fields: {missing}")
    return summary


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def demo() -> int:
    """Run all free, unauthenticated TikHub demo endpoints once."""
    summaries = []
    with httpx.Client(
        timeout=30.0,
        follow_redirects=True,
        headers={"User-Agent": "douyin-research-platform-v0-smoke/1.0"},
    ) as client:
        for label, url in DEMO_URLS.items():
            response = client.get(url)
            response.raise_for_status()
            payload = validate_envelope(response.json(), label)
            raw_path = save_raw(label, payload)
            summaries.append(
                summarize_demo(
                    label,
                    payload,
                    http_status=response.status_code,
                    raw_path=raw_path,
                )
            )

    print(json.dumps(summaries, ensure_ascii=False, indent=2))
    return 0


def require_paid() -> str:
    if os.getenv(PAID_GATE) != "YES":
        raise SmokeFailure(
            f"paid smoke is disabled. Set {PAID_GATE}=YES explicitly before running."
        )
    api_key = os.getenv(API_KEY_ENV)
    if not api_key:
        raise SmokeFailure(f"{API_KEY_ENV} is required for paid smoke.")
    return api_key


def get_sdk(api_key: str):
    try:
        from tikhub import TikHub, __version__
    except ImportError as exc:
        raise SmokeFailure(
            'TikHub SDK missing. Install pinned dependency first: pip install "tikhub==2.1.1"'
        ) from exc
    # A logical smoke call must not fan out into provider-billed retry attempts.
    return TikHub(api_key=api_key, max_retries=SDK_MAX_RETRIES), __version__


def save_and_summarize(label: str, payload: Any, sdk_version: str) -> dict[str, Any]:
    env = validate_envelope(payload, label)
    raw_path = save_raw(label, env)
    data = env.get("data")
    items = first_list(data)
    summary = {
        "label": label,
        "sdk_version": sdk_version,
        "request_id_present": bool(env.get("request_id")),
        "router": env.get("router"),
        "data_type": type(data).__name__,
        "first_list_count": len(items) if items is not None else None,
        "raw_path": str(raw_path),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def paid_billboard() -> int:
    api_key = require_paid()
    client, version = get_sdk(api_key)
    with client:
        payload = client.douyin_billboard.fetch_hot_total_low_fan_list(
            page=1,
            page_size=5,
            date_window=24,
            tags=[],
        )
    save_and_summarize("billboard_low_fan_24h", payload, version)
    return 0


def paid_creator() -> int:
    api_key = require_paid()
    client, version = get_sdk(api_key)
    with client:
        # Travel, highest popularity, last 24h.
        payload = client.douyin_creator.fetch_creator_material_center_billboard(
            billboard_tag=334,
            order_key=4,
            time_filter=1,
        )
    save_and_summarize("creator_material_travel_24h", payload, version)
    return 0


def paid_search() -> int:
    api_key = require_paid()
    client, version = get_sdk(api_key)
    with client:
        payload = client.douyin_search.fetch_video_search_v2(
            keyword="神话",
            cursor=0,
            sort_type="0",
            publish_time="7",
            filter_duration="0-1",
            content_type="1",
            search_id="",
            backtrace="",
        )
    save_and_summarize("search_video_myth_7d", payload, version)
    return 0


def paid_appv3(aweme_id: str) -> int:
    api_key = require_paid()
    if not aweme_id:
        raise SmokeFailure("--aweme-id is required for appv3 smoke")
    client, version = get_sdk(api_key)
    with client:
        payload = client.douyin_app_v3.fetch_multi_video_v2(body=[aweme_id])
    save_and_summarize("appv3_batch_video_1", payload, version)
    return 0


def all_paid(aweme_id: str) -> int:
    # Intentionally small: exactly four platform calls, no pagination.
    print(
        "Running PAID smoke: 4 platform calls max, no pagination. "
        "Provider-side pricing still applies.",
        file=sys.stderr,
    )
    paid_billboard()
    paid_creator()
    paid_search()
    paid_appv3(aweme_id)
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "mode",
        choices=["demo", "billboard", "creator", "search", "appv3", "all-paid"],
        nargs="?",
        default="demo",
    )
    p.add_argument("--aweme-id", default="")
    args = p.parse_args()

    try:
        if args.mode == "demo":
            return demo()
        if args.mode == "billboard":
            return paid_billboard()
        if args.mode == "creator":
            return paid_creator()
        if args.mode == "search":
            return paid_search()
        if args.mode == "appv3":
            return paid_appv3(args.aweme_id)
        return all_paid(args.aweme_id)
    except (SmokeFailure, httpx.HTTPError) as exc:
        print(f"SMOKE FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
