"""TikHub payload -> provider-neutral observations."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, Iterable

from .errors import ProviderSchemaError
from .types import AccountRef, CommentSample, MetricSnapshotInput, VideoObservation, VideoRef


def validate_tikhub_envelope(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ProviderSchemaError(f"TikHub response must be object, got {type(payload).__name__}")
    code = payload.get("code")
    if code != 200:
        message = str(payload.get("message") or payload.get("message_zh") or "TikHub business error")
        lower = message.lower()
        if any(x in lower for x in ("balance", "credit", "余额")):
            from .errors import ProviderBalanceError

            raise ProviderBalanceError(message)
        if any(x in lower for x in ("rate limit", "too many", "频率", "限流")):
            from .errors import ProviderRateLimitError

            raise ProviderRateLimitError(message)
        if any(x in lower for x in ("token", "auth", "unauthorized", "认证")):
            from .errors import ProviderAuthError

            raise ProviderAuthError(message)
        from .errors import ProviderPermanentError

        raise ProviderPermanentError(f"TikHub code={code!r}: {message}")
    return payload


def normalize_video_observations(
    payload: dict[str, Any],
    *,
    endpoint_key: str,
    raw_ref: str | None,
    observed_at: datetime,
) -> list[VideoObservation]:
    """Extract unique aweme-like objects anywhere below data.

    This intentionally keys on Douyin's stable aweme_id rather than endpoint-
    specific nesting.  V0 fixtures will later tighten per-endpoint mappings.
    """
    validated = validate_tikhub_envelope(payload)
    data = validated.get("data")
    seen: set[str] = set()
    observations: list[VideoObservation] = []
    for obj in _walk_dicts(data):
        aweme_id = _as_str(obj.get("aweme_id"))
        if not aweme_id or aweme_id in seen:
            continue
        seen.add(aweme_id)
        observations.append(
            _normalize_aweme(
                obj,
                endpoint_key=endpoint_key,
                raw_ref=raw_ref,
                observed_at=observed_at,
            )
        )
    return observations


def _normalize_aweme(
    obj: dict[str, Any],
    *,
    endpoint_key: str,
    raw_ref: str | None,
    observed_at: datetime,
) -> VideoObservation:
    aweme_id = _as_str(obj.get("aweme_id"))
    if not aweme_id:
        raise ProviderSchemaError("aweme object missing aweme_id")

    author = obj.get("author") if isinstance(obj.get("author"), dict) else {}
    statistics = obj.get("statistics") if isinstance(obj.get("statistics"), dict) else {}
    video_obj = obj.get("video") if isinstance(obj.get("video"), dict) else {}

    sec_uid = _first_str(author, "sec_uid", "sec_user_id")
    uid = _first_str(author, "uid", "user_id", "id")
    platform_account_id = sec_uid or uid

    follower_count = _first_int(author, "follower_count")
    if follower_count is None and isinstance(author.get("stats"), dict):
        follower_count = _first_int(author["stats"], "follower_count")

    account = None
    if platform_account_id:
        account = AccountRef(
            provider="tikhub",
            platform="douyin",
            platform_account_id=platform_account_id,
            sec_user_id=sec_uid,
            nickname=_first_str(author, "nickname", "unique_id"),
            profile_url=None,
            follower_count=follower_count,
            observed_at=observed_at,
            raw_ref=raw_ref,
        )

    create_time = _first_int(obj, "create_time")
    published_at = (
        datetime.fromtimestamp(create_time, tz=timezone.utc) if create_time is not None else None
    )

    duration_ms = _first_int(obj, "duration")
    if duration_ms is None:
        duration_ms = _first_int(video_obj, "duration")

    description = _first_str(obj, "desc", "description")
    title = _first_str(obj, "item_title", "title") or description
    share_url = _first_str(obj, "share_url")

    video = VideoRef(
        provider="tikhub",
        platform="douyin",
        platform_video_id=aweme_id,
        account_platform_id=platform_account_id,
        title=title,
        description=description,
        source_url=share_url,
        published_at=published_at,
        duration_ms=duration_ms,
        availability_status="available",
        observed_at=observed_at,
        raw_ref=raw_ref,
    )

    metrics = MetricSnapshotInput(
        platform="douyin",
        video_platform_id=aweme_id,
        captured_at=observed_at,
        provider="tikhub",
        source_endpoint=endpoint_key,
        play_count=_first_int(statistics, "play_count"),
        like_count=_first_int(statistics, "digg_count", "like_count"),
        comment_count=_first_int(statistics, "comment_count"),
        share_count=_first_int(statistics, "share_count"),
        collect_count=_first_int(statistics, "collect_count"),
        author_follower_count=follower_count,
        metric_status={},
        raw_ref=raw_ref,
    )
    _fill_metric_status(metrics)

    return VideoObservation(video=video, account=account, metrics=metrics)


def _fill_metric_status(metrics: MetricSnapshotInput) -> None:
    for name in (
        "play_count",
        "like_count",
        "comment_count",
        "share_count",
        "collect_count",
        "author_follower_count",
    ):
        metrics.metric_status[name] = "available" if getattr(metrics, name) is not None else "unavailable"



def normalize_comment_samples(
    payload: dict[str, Any],
    *,
    video_platform_id: str,
    endpoint_key: str,
    observed_at: datetime,
    sample_reason: str = "top",
) -> list[CommentSample]:
    """Extract and deduplicate comments without leaking provider nesting upward."""
    validated = validate_tikhub_envelope(payload)
    seen: set[str] = set()
    comments: list[CommentSample] = []
    for obj in _walk_dicts(validated.get("data")):
        comment_id = _first_str(obj, "cid", "comment_id")
        text = _first_str(obj, "text", "content", "comment_text")
        if not comment_id or text is None or comment_id in seen:
            continue
        seen.add(comment_id)
        create_time = _first_int(obj, "create_time")
        published_at = (
            datetime.fromtimestamp(create_time, tz=timezone.utc)
            if create_time is not None
            else None
        )
        comments.append(
            CommentSample(
                provider="tikhub",
                platform="douyin",
                source_endpoint=endpoint_key,
                video_platform_id=video_platform_id,
                platform_comment_id=comment_id,
                text=text,
                like_count=_first_int(obj, "digg_count", "like_count"),
                published_at=published_at,
                reply_count=_first_int(
                    obj,
                    "reply_comment_total",
                    "reply_count",
                    "reply_comment_count",
                    "reply_total",
                ),
                sample_reason=sample_reason,
                observed_at=observed_at,
            )
        )
    return comments


def extract_pagination(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload, dict) else None
    direct = [payload, data] if isinstance(data, dict) else [payload]
    nested = list(_walk_dicts(data))
    out: dict[str, Any] = {}
    for obj in [*direct, *nested]:
        if not isinstance(obj, dict):
            continue
        for key in ("cursor", "max_cursor", "has_more", "search_id", "page", "page_size", "total"):
            if key in obj and key not in out:
                out[key] = obj[key]
    return out


def _walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)
    elif isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                return
            if parsed != value:
                yield from _walk_dicts(parsed)


def _first_str(obj: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = _as_str(obj.get(key))
        if value:
            return value
    return None


def _first_int(obj: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = obj.get(key)
        if value is None or isinstance(value, bool):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
