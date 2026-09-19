#!/usr/bin/env python3
"""TikHub V0 smoke test.

Default behavior is FREE and does not require TIKHUB_API_KEY:
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

DEMO_URL = "https://api.tikhub.io/api/v1/demo/douyin/web/fetch_one_video"
OUT_DIR = Path("tmp/tikhub-v0")
PAID_GATE = "TIKHUB_ENABLE_PAID_SMOKE"
API_KEY_ENV = "TIKHUB_API_KEY"


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


def demo() -> int:
    """Free, unauthenticated fixed Douyin item demo."""
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        r = client.get(DEMO_URL)
        r.raise_for_status()
        payload = validate_envelope(r.json(), "demo")

    raw_path = save_raw("demo_douyin_web_video", payload)
    aweme = dig(payload, "data", "aweme_detail") or {}
    author = aweme.get("author") if isinstance(aweme, dict) else {}
    video = aweme.get("video") if isinstance(aweme, dict) else {}

    summary = {
        "http_status": r.status_code,
        "tikhub_code": payload.get("code"),
        "request_id": payload.get("request_id"),
        "router": payload.get("router"),
        "aweme_id": aweme.get("aweme_id"),
        "author_sec_uid": author.get("sec_uid") if isinstance(author, dict) else None,
        "author_uid": author.get("uid") if isinstance(author, dict) else None,
        "nickname": author.get("nickname") if isinstance(author, dict) else None,
        "follower_count": author.get("follower_count") if isinstance(author, dict) else None,
        "duration_ms": aweme.get("duration"),
        "desc_present": bool(aweme.get("desc")),
        "caption_present": bool(aweme.get("caption")),
        "video_field_present": isinstance(video, dict),
        "raw_path": str(raw_path),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    required = ["aweme_id", "author_sec_uid", "duration_ms"]
    missing = [x for x in required if not summary.get(x)]
    if missing:
        raise SmokeFailure(f"demo missing required canonical fields: {missing}")
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
    return TikHub(api_key=api_key), __version__


def save_and_summarize(label: str, payload: Any, sdk_version: str) -> dict[str, Any]:
    env = validate_envelope(payload, label)
    raw_path = save_raw(label, env)
    data = env.get("data")
    items = first_list(data)
    summary = {
        "label": label,
        "sdk_version": sdk_version,
        "request_id": env.get("request_id"),
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
