#!/usr/bin/env python3
"""TikHub V0 paid batch 2: account -> posts -> detail -> comments, max 10 calls.

The probe is deliberately narrow:
- requires TIKHUB_ENABLE_PAID_BATCH2=BATCH2_10
- requires TIKHUB_API_KEY
- disables SDK retries
- never prints account, video, comment, request, or API-key values
- writes raw responses only to gitignored tmp/tikhub-v0-batch2/
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

OUT_DIR = Path("tmp/tikhub-v0-batch2")
API_KEY_ENV = "TIKHUB_API_KEY"
GATE_ENV = "TIKHUB_ENABLE_PAID_BATCH2"
GATE_VALUE = "BATCH2_10"
MAX_CALLS = 10


class ProbeFailure(RuntimeError):
    pass


def now_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child)


def first_list(value: Any) -> list[Any] | None:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for child in value.values():
            found = first_list(child)
            if found is not None:
                return found
    return None


def first_value(value: Any, keys: tuple[str, ...]) -> Any:
    for obj in walk_dicts(value):
        for key in keys:
            if obj.get(key) is not None:
                return obj[key]
    return None


def validate_envelope(payload: Any, label: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ProbeFailure(f"{label}: non-dict response")
    if payload.get("code") != 200:
        raise ProbeFailure(f"{label}: TikHub business code={payload.get('code')!r}")
    return payload


def save_raw(label: str, payload: dict[str, Any]) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{now_slug()}_{label}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def opaque_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]


@dataclass
class CallBudget:
    max_calls: int = MAX_CALLS
    calls: int = 0

    @property
    def remaining(self) -> int:
        return self.max_calls - self.calls

    def call(self, label: str, method: Callable[..., Any], **kwargs: Any) -> dict[str, Any]:
        if self.calls >= self.max_calls:
            raise ProbeFailure(f"call budget exhausted before {label}")
        self.calls += 1
        payload = validate_envelope(method(**kwargs), label)
        save_raw(label, payload)
        return payload


def follower_bucket(value: Any) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "unknown"
    if value < 1_000:
        return "lt_1k"
    if value < 10_000:
        return "1k_10k"
    if value < 100_000:
        return "10k_100k"
    if value < 500_000:
        return "100k_500k"
    if value < 1_000_000:
        return "500k_1m"
    return "gte_1m"


def user_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}
    for obj in walk_dicts(payload.get("data")):
        sec_uid = obj.get("sec_uid") or obj.get("sec_user_id")
        if not isinstance(sec_uid, str) or not sec_uid:
            continue
        follower = obj.get("follower_count")
        if follower is None and isinstance(obj.get("user_info"), dict):
            follower = obj["user_info"].get("follower_count")
        current = candidates.setdefault(
            sec_uid,
            {"sec_uid": sec_uid, "follower_count": follower},
        )
        if current["follower_count"] is None and follower is not None:
            current["follower_count"] = follower
    return list(candidates.values())


def choose_ordinary_user(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not candidates:
        raise ProbeFailure("user search returned no stable sec_uid")
    for candidate in candidates:
        followers = candidate.get("follower_count")
        if isinstance(followers, (int, float)) and 1_000 <= followers < 500_000:
            return candidate
    with_count = [x for x in candidates if isinstance(x.get("follower_count"), (int, float))]
    return min(with_count, key=lambda x: x["follower_count"]) if with_count else candidates[0]


def video_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    videos: dict[str, dict[str, Any]] = {}
    for obj in walk_dicts(payload.get("data")):
        aweme_id = obj.get("aweme_id")
        if not isinstance(aweme_id, str) or not aweme_id:
            continue
        statistics = obj.get("statistics") if isinstance(obj.get("statistics"), dict) else {}
        item = videos.setdefault(
            aweme_id,
            {
                "aweme_id": aweme_id,
                "comment_count": statistics.get("comment_count"),
                "statistics": statistics,
            },
        )
        if not item["statistics"] and statistics:
            item["statistics"] = statistics
            item["comment_count"] = statistics.get("comment_count")
    return list(videos.values())


def choose_comment_rich_video(videos: list[dict[str, Any]]) -> dict[str, Any]:
    if not videos:
        raise ProbeFailure("user posts returned no stable aweme_id")
    return max(
        videos,
        key=lambda item: item.get("comment_count")
        if isinstance(item.get("comment_count"), (int, float))
        else -1,
    )


def metric_coverage(videos: list[dict[str, Any]]) -> dict[str, int]:
    fields = ("play_count", "digg_count", "comment_count", "share_count", "collect_count")
    return {
        field: sum(
            1
            for item in videos
            if isinstance(item.get("statistics"), dict)
            and item["statistics"].get(field) is not None
        )
        for field in fields
    }


def safe_shape(label: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    items = first_list(data)
    return {
        "label": label,
        "code": payload.get("code"),
        "request_id_present": bool(payload.get("request_id")),
        "router": payload.get("router"),
        "data_type": type(data).__name__,
        "first_list_count": len(items) if items is not None else None,
    }


def numeric_leaves(value: Any, prefix: str = "") -> dict[str, float]:
    found: dict[str, float] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else key
            found.update(numeric_leaves(child, child_prefix))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.update(numeric_leaves(child, f"{prefix}[{index}]"))
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        found[prefix] = float(value)
    return found


def usage_delta_summary(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_values = numeric_leaves(before.get("data"))
    after_values = numeric_leaves(after.get("data"))
    changed = sorted(
        key
        for key in before_values.keys() & after_values.keys()
        if before_values[key] != after_values[key]
    )
    return {
        "usage_change_detected": bool(changed),
        "changed_numeric_field_count": len(changed),
        "changed_field_names": [key.split(".")[-1] for key in changed[:12]],
    }


def public_price_summary(payload: dict[str, Any]) -> dict[str, float]:
    allowed = ("price", "cost", "discount", "request")
    values = numeric_leaves(payload.get("data"))
    return {
        key: value
        for key, value in values.items()
        if any(token in key.lower() for token in allowed)
    }


def pagination(payload: dict[str, Any]) -> tuple[Any, Any]:
    return (
        first_value(payload.get("data"), ("cursor", "max_cursor")),
        first_value(payload.get("data"), ("has_more",)),
    )


def require_environment() -> str:
    if os.getenv(GATE_ENV) != GATE_VALUE:
        raise ProbeFailure(f"{GATE_ENV} must equal {GATE_VALUE}")
    api_key = os.getenv(API_KEY_ENV)
    if not api_key:
        raise ProbeFailure(f"{API_KEY_ENV} is missing")
    return api_key


def supports_keyword(callable_obj: Callable[..., Any], name: str) -> bool:
    parameters = inspect.signature(callable_obj).parameters
    return name in parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )


def preflight_sdk(client: Any) -> None:
    required = {
        "tikhub_user": {
            "get_user_daily_usage": (),
            "calculate_price": ("endpoint", "request_per_day"),
        },
        "douyin_search": {
            "fetch_user_search_v2": ("keyword", "cursor"),
        },
        "douyin_app_v3": {
            "handler_user_profile": ("sec_user_id",),
            "fetch_user_post_videos": ("sec_user_id", "max_cursor", "count", "sort_type"),
            "fetch_multi_video_v2": ("body",),
            "fetch_video_comments": ("aweme_id", "cursor", "count"),
            "fetch_video_comment_replies": ("item_id", "comment_id", "cursor", "count"),
        },
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


def run() -> int:
    api_key = require_environment()
    from tikhub import TikHub, __version__

    if not supports_keyword(TikHub, "max_retries"):
        raise ProbeFailure("SDK constructor cannot enforce max_retries=0")

    budget = CallBudget()
    summaries: list[dict[str, Any]] = []
    client = TikHub(api_key=api_key, max_retries=0)
    preflight_sdk(client)

    with client:
        usage_before = budget.call(
            "usage_before",
            client.tikhub_user.get_user_daily_usage,
        )
        summaries.append(safe_shape("usage_before", usage_before))

        price = budget.call(
            "price_comments",
            client.tikhub_user.calculate_price,
            endpoint="/api/v1/douyin/app/v3/fetch_video_comments",
            request_per_day=100,
        )
        price_summary = safe_shape("price_comments", price)
        price_summary["public_pricing"] = public_price_summary(price)
        summaries.append(price_summary)

        search1 = budget.call(
            "user_search_page1",
            client.douyin_search.fetch_user_search_v2,
            keyword="秦皇岛旅游",
            cursor=0,
        )
        candidates = user_candidates(search1)
        selected_user = choose_ordinary_user(candidates)
        search_cursor, search_has_more = pagination(search1)
        search_summary = safe_shape("user_search_page1", search1)
        search_summary.update(
            {
                "unique_user_candidate_count": len(candidates),
                "selected_follower_bucket": follower_bucket(
                    selected_user.get("follower_count")
                ),
                "cursor_present": search_cursor is not None,
                "has_more": search_has_more,
            }
        )
        summaries.append(search_summary)

        if search_cursor not in (None, "", 0, "0") and search_has_more not in (0, False):
            search2 = budget.call(
                "user_search_page2",
                client.douyin_search.fetch_user_search_v2,
                keyword="秦皇岛旅游",
                cursor=search_cursor,
            )
            page2_candidates = user_candidates(search2)
            search2_summary = safe_shape("user_search_page2", search2)
            search2_summary["unique_user_candidate_count"] = len(page2_candidates)
            summaries.append(search2_summary)

        profile = budget.call(
            "user_profile",
            client.douyin_app_v3.handler_user_profile,
            sec_user_id=selected_user["sec_uid"],
        )
        profile_summary = safe_shape("user_profile", profile)
        profile_summary.update(
            {
                "follower_count_present": first_value(
                    profile.get("data"), ("follower_count",)
                )
                is not None,
                "following_count_present": first_value(
                    profile.get("data"), ("following_count",)
                )
                is not None,
                "total_favorited_present": first_value(
                    profile.get("data"), ("total_favorited",)
                )
                is not None,
            }
        )
        summaries.append(profile_summary)

        posts1 = budget.call(
            "user_posts_page1",
            client.douyin_app_v3.fetch_user_post_videos,
            sec_user_id=selected_user["sec_uid"],
            max_cursor=0,
            count=10,
            sort_type=0,
        )
        videos = video_candidates(posts1)
        posts_cursor, posts_has_more = pagination(posts1)
        posts1_summary = safe_shape("user_posts_page1", posts1)
        posts1_summary.update(
            {
                "unique_video_count": len(videos),
                "metric_coverage": metric_coverage(videos),
                "cursor_present": posts_cursor is not None,
                "has_more": posts_has_more,
            }
        )
        summaries.append(posts1_summary)

        if (
            posts_cursor not in (None, "", 0, "0")
            and posts_has_more not in (0, False)
            and budget.remaining > 3
        ):
            posts2 = budget.call(
                "user_posts_page2",
                client.douyin_app_v3.fetch_user_post_videos,
                sec_user_id=selected_user["sec_uid"],
                max_cursor=posts_cursor,
                count=10,
                sort_type=0,
            )
            page2_videos = video_candidates(posts2)
            posts2_summary = safe_shape("user_posts_page2", posts2)
            posts2_summary.update(
                {
                    "unique_video_count": len(page2_videos),
                    "metric_coverage": metric_coverage(page2_videos),
                }
            )
            summaries.append(posts2_summary)
            videos.extend(
                video
                for video in page2_videos
                if video["aweme_id"] not in {item["aweme_id"] for item in videos}
            )

        selected_video = choose_comment_rich_video(videos)
        detail = budget.call(
            "video_detail",
            client.douyin_app_v3.fetch_multi_video_v2,
            body=[selected_video["aweme_id"]],
        )
        detail_videos = video_candidates(detail)
        detail_summary = safe_shape("video_detail", detail)
        detail_summary.update(
            {
                "unique_video_count": len(detail_videos),
                "metric_coverage": metric_coverage(detail_videos),
                "selected_video_fingerprint": opaque_id(selected_video["aweme_id"]),
            }
        )
        summaries.append(detail_summary)

        comments = budget.call(
            "video_comments_page1",
            client.douyin_app_v3.fetch_video_comments,
            aweme_id=selected_video["aweme_id"],
            cursor=0,
            count=20,
        )
        comment_items = [
            obj
            for obj in walk_dicts(comments.get("data"))
            if isinstance(obj.get("cid") or obj.get("comment_id"), str)
        ]
        comment_ids = {
            str(obj.get("cid") or obj.get("comment_id"))
            for obj in comment_items
        }
        comments_summary = safe_shape("video_comments_page1", comments)
        comments_summary.update(
            {
                "unique_comment_count": len(comment_ids),
                "cursor_present": first_value(
                    comments.get("data"), ("cursor",)
                )
                is not None,
                "has_more": first_value(comments.get("data"), ("has_more",)),
            }
        )
        summaries.append(comments_summary)

        if comment_items and budget.remaining > 1:
            selected_comment = comment_items[0]
            comment_id = str(selected_comment.get("cid") or selected_comment.get("comment_id"))
            replies = budget.call(
                "comment_replies_page1",
                client.douyin_app_v3.fetch_video_comment_replies,
                item_id=selected_video["aweme_id"],
                comment_id=comment_id,
                cursor=0,
                count=20,
            )
            reply_ids = {
                str(obj.get("cid") or obj.get("comment_id"))
                for obj in walk_dicts(replies.get("data"))
                if isinstance(obj.get("cid") or obj.get("comment_id"), str)
            }
            replies_summary = safe_shape("comment_replies_page1", replies)
            replies_summary["unique_reply_count"] = len(reply_ids)
            summaries.append(replies_summary)

        usage_after = budget.call(
            "usage_after",
            client.tikhub_user.get_user_daily_usage,
        )
        usage_summary = safe_shape("usage_after", usage_after)
        usage_summary.update(usage_delta_summary(usage_before, usage_after))
        summaries.append(usage_summary)

    result = {
        "sdk_version": __version__,
        "call_budget_max": budget.max_calls,
        "calls_completed": budget.calls,
        "raw_files_ephemeral": True,
        "summaries": summaries,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    try:
        return run()
    except ProbeFailure as exc:
        print(f"BATCH2 FAILED: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"BATCH2 FAILED: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
