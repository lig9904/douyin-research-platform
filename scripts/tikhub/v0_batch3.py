#!/usr/bin/env python3
"""TikHub V0 paid batch 3: pagination, deduplication, replies, max 10 calls.

Safety properties:
- requires TIKHUB_ENABLE_PAID_BATCH3=BATCH3_10
- requires TIKHUB_API_KEY
- disables SDK retries
- never prints account, video, comment, request, nickname, or API-key values
- writes raw responses only to gitignored tmp/tikhub-v0-batch3/
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from v0_batch2 import (
    CallBudget,
    ProbeFailure,
    choose_comment_rich_video,
    choose_ordinary_user,
    first_value,
    follower_bucket,
    metric_coverage,
    pagination,
    preflight_sdk,
    public_price_summary,
    safe_shape,
    supports_keyword,
    usage_delta_summary,
    user_candidates,
    validate_envelope,
    video_candidates,
)

OUT_DIR = Path("tmp/tikhub-v0-batch3")
API_KEY_ENV = "TIKHUB_API_KEY"
GATE_ENV = "TIKHUB_ENABLE_PAID_BATCH3"
GATE_VALUE = "BATCH3_10"
MAX_CALLS = 10


def save_raw(label: str, payload: dict[str, Any]) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{label}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


class Batch3Budget(CallBudget):
    def call(self, label: str, method, **kwargs: Any) -> dict[str, Any]:
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


def stable_ids(payload: dict[str, Any], keys: tuple[str, ...]) -> set[str]:
    from v0_batch2 import walk_dicts

    found: set[str] = set()
    for obj in walk_dicts(payload.get("data")):
        value = next((obj.get(key) for key in keys if obj.get(key) is not None), None)
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            found.add(str(value))
    return found


def page_relation(first: set[str], second: set[str]) -> dict[str, int]:
    return {
        "page1_unique_count": len(first),
        "page2_unique_count": len(second),
        "cross_page_overlap_count": len(first & second),
        "combined_unique_count": len(first | second),
    }


def cursor_advanced(first_cursor: Any, second_cursor: Any) -> bool:
    return (
        first_cursor is not None
        and second_cursor is not None
        and str(first_cursor) != str(second_cursor)
    )


def comment_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    from v0_batch2 import walk_dicts

    candidates: dict[str, dict[str, Any]] = {}
    reply_keys = (
        "reply_comment_total",
        "reply_count",
        "reply_comment_count",
        "reply_total",
    )
    for obj in walk_dicts(payload.get("data")):
        comment_id = obj.get("cid") or obj.get("comment_id")
        if not isinstance(comment_id, (str, int)) or isinstance(comment_id, bool):
            continue
        reply_count = next(
            (obj.get(key) for key in reply_keys if obj.get(key) is not None),
            None,
        )
        key = str(comment_id)
        current = candidates.setdefault(
            key,
            {"comment_id": key, "reply_count": reply_count},
        )
        if current["reply_count"] is None and reply_count is not None:
            current["reply_count"] = reply_count
    return list(candidates.values())


def choose_reply_candidate(comments: list[dict[str, Any]]) -> dict[str, Any]:
    if not comments:
        raise ProbeFailure("comments returned no stable comment ID")
    with_replies = [
        item
        for item in comments
        if isinstance(item.get("reply_count"), (int, float))
        and not isinstance(item.get("reply_count"), bool)
        and item["reply_count"] > 0
    ]
    if with_replies:
        return max(with_replies, key=lambda item: item["reply_count"])
    return comments[0]


def emit(summary: dict[str, Any]) -> None:
    print("BATCH3_PROGRESS " + json.dumps(summary, ensure_ascii=False, sort_keys=True))


def require_next_page(label: str, cursor: Any, has_more: Any) -> None:
    if cursor in (None, ""):
        raise ProbeFailure(f"{label}: missing cursor")
    if has_more in (False, 0, "0", None):
        raise ProbeFailure(f"{label}: no second page available")


def run() -> int:
    api_key = require_environment()
    from tikhub import TikHub, __version__

    if not supports_keyword(TikHub, "max_retries"):
        raise ProbeFailure("SDK constructor cannot enforce max_retries=0")

    budget = Batch3Budget(max_calls=MAX_CALLS)
    summaries: list[dict[str, Any]] = []
    client = TikHub(api_key=api_key, max_retries=0)
    preflight_sdk(client)

    with client:
        usage_before = budget.call(
            "usage_before",
            client.tikhub_user.get_user_daily_usage,
        )
        summary = safe_shape("usage_before", usage_before)
        summaries.append(summary)
        emit(summary)

        search = budget.call(
            "user_search_v1",
            client.douyin_search.fetch_user_search,
            keyword="秦皇岛旅游",
            cursor=0,
            douyin_user_fans=None,
            douyin_user_type=None,
            search_id="",
        )
        users = user_candidates(search)
        selected_user = choose_ordinary_user(users)
        summary = safe_shape("user_search_v1", search)
        summary.update(
            {
                "unique_user_candidate_count": len(users),
                "selected_follower_bucket": follower_bucket(
                    selected_user.get("follower_count")
                ),
            }
        )
        summaries.append(summary)
        emit(summary)

        posts1 = budget.call(
            "user_posts_page1",
            client.douyin_app_v3.fetch_user_post_videos,
            sec_user_id=selected_user["sec_uid"],
            max_cursor=0,
            count=10,
            sort_type=0,
        )
        post_cursor1, post_more1 = pagination(posts1)
        require_next_page("user_posts_page1", post_cursor1, post_more1)
        videos1 = video_candidates(posts1)
        post_ids1 = {item["aweme_id"] for item in videos1}
        summary = safe_shape("user_posts_page1", posts1)
        summary.update(
            {
                "unique_video_count": len(post_ids1),
                "metric_coverage": metric_coverage(videos1),
                "cursor_present": True,
                "has_more": post_more1,
            }
        )
        summaries.append(summary)
        emit(summary)

        posts2 = budget.call(
            "user_posts_page2",
            client.douyin_app_v3.fetch_user_post_videos,
            sec_user_id=selected_user["sec_uid"],
            max_cursor=post_cursor1,
            count=10,
            sort_type=0,
        )
        post_cursor2, post_more2 = pagination(posts2)
        videos2 = video_candidates(posts2)
        post_ids2 = {item["aweme_id"] for item in videos2}
        summary = safe_shape("user_posts_page2", posts2)
        summary.update(page_relation(post_ids1, post_ids2))
        summary.update(
            {
                "metric_coverage": metric_coverage(videos2),
                "cursor_present": post_cursor2 is not None,
                "cursor_advanced": cursor_advanced(post_cursor1, post_cursor2),
                "has_more": post_more2,
            }
        )
        summaries.append(summary)
        emit(summary)

        selected_video = choose_comment_rich_video(videos1 + videos2)
        detail = budget.call(
            "video_detail",
            client.douyin_app_v3.fetch_multi_video_v2,
            body=[selected_video["aweme_id"]],
        )
        detail_videos = video_candidates(detail)
        summary = safe_shape("video_detail", detail)
        summary.update(
            {
                "unique_video_count": len(detail_videos),
                "metric_coverage": metric_coverage(detail_videos),
            }
        )
        summaries.append(summary)
        emit(summary)

        comments1 = budget.call(
            "video_comments_page1",
            client.douyin_app_v3.fetch_video_comments,
            aweme_id=selected_video["aweme_id"],
            cursor=0,
            count=20,
        )
        comment_cursor1, comment_more1 = pagination(comments1)
        require_next_page("video_comments_page1", comment_cursor1, comment_more1)
        comment_ids1 = stable_ids(comments1, ("cid", "comment_id"))
        comments_for_reply = comment_candidates(comments1)
        summary = safe_shape("video_comments_page1", comments1)
        summary.update(
            {
                "unique_comment_count": len(comment_ids1),
                "cursor_present": True,
                "has_more": comment_more1,
            }
        )
        summaries.append(summary)
        emit(summary)

        comments2 = budget.call(
            "video_comments_page2",
            client.douyin_app_v3.fetch_video_comments,
            aweme_id=selected_video["aweme_id"],
            cursor=comment_cursor1,
            count=20,
        )
        comment_cursor2, comment_more2 = pagination(comments2)
        comment_ids2 = stable_ids(comments2, ("cid", "comment_id"))
        comments_for_reply.extend(comment_candidates(comments2))
        summary = safe_shape("video_comments_page2", comments2)
        summary.update(page_relation(comment_ids1, comment_ids2))
        summary.update(
            {
                "cursor_present": comment_cursor2 is not None,
                "cursor_advanced": cursor_advanced(comment_cursor1, comment_cursor2),
                "has_more": comment_more2,
            }
        )
        summaries.append(summary)
        emit(summary)

        selected_comment = choose_reply_candidate(comments_for_reply)
        replies = budget.call(
            "comment_replies_page1",
            client.douyin_app_v3.fetch_video_comment_replies,
            item_id=selected_video["aweme_id"],
            comment_id=selected_comment["comment_id"],
            cursor=0,
            count=20,
        )
        reply_ids = stable_ids(replies, ("cid", "comment_id"))
        reply_cursor, reply_more = pagination(replies)
        summary = safe_shape("comment_replies_page1", replies)
        summary.update(
            {
                "selected_comment_reported_replies": (
                    isinstance(selected_comment.get("reply_count"), (int, float))
                    and not isinstance(selected_comment.get("reply_count"), bool)
                    and selected_comment["reply_count"] > 0
                ),
                "unique_reply_count": len(reply_ids),
                "cursor_present": reply_cursor is not None,
                "has_more": reply_more,
            }
        )
        summaries.append(summary)
        emit(summary)

        price = budget.call(
            "price_comments",
            client.tikhub_user.calculate_price,
            endpoint="/api/v1/douyin/app/v3/fetch_video_comments",
            request_per_day=100,
        )
        summary = safe_shape("price_comments", price)
        summary["public_pricing"] = public_price_summary(price)
        summaries.append(summary)
        emit(summary)

        usage_after = budget.call(
            "usage_after",
            client.tikhub_user.get_user_daily_usage,
        )
        summary = safe_shape("usage_after", usage_after)
        summary.update(usage_delta_summary(usage_before, usage_after))
        summaries.append(summary)
        emit(summary)

    result = {
        "sdk_version": __version__,
        "call_budget_max": budget.max_calls,
        "calls_completed": budget.calls,
        "sdk_retries": 0,
        "raw_files_ephemeral": True,
        "summaries": summaries,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def main() -> int:
    try:
        return run()
    except ProbeFailure as exc:
        print(f"BATCH3 FAILED: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"BATCH3 FAILED: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
