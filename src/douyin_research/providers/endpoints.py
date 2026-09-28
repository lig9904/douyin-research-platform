"""Read-only TikHub endpoint allowlist for V1.

No generic endpoint passthrough is exposed to callers.
"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class EndpointSpec:
    key: str
    http_method: str
    path: str
    sdk_resource: str | None
    sdk_method: str | None
    request_style: str  # query | json | none
    cache_ttl_seconds: int
    paid: bool = True
    unit_cost_usd: float | None = None
    price_source: str | None = None
    pricing_version: str | None = None


ENDPOINTS: dict[str, EndpointSpec] = {
    "douyin.billboard.low_fan": EndpointSpec(
        key="douyin.billboard.low_fan",
        http_method="POST",
        path="/api/v1/douyin/billboard/fetch_hot_total_low_fan_list",
        sdk_resource="douyin_billboard",
        sdk_method="fetch_hot_total_low_fan_list",
        request_style="json",
        cache_ttl_seconds=15 * 60,
        # Verified with TikHub calculate_price on 2026-09-20; public docs
        # identify USD 0.001 as the base price for most successful requests.
        unit_cost_usd=0.001,
        price_source="tikhub.calculate_price",
        pricing_version="verified-2026-09-20",
    ),
    "douyin.creator.material": EndpointSpec(
        key="douyin.creator.material",
        http_method="GET",
        path="/api/v1/douyin/creator/fetch_creator_material_center_billboard",
        sdk_resource="douyin_creator",
        sdk_method="fetch_creator_material_center_billboard",
        request_style="query",
        cache_ttl_seconds=30 * 60,
    ),
    "douyin.search.video_v2": EndpointSpec(
        key="douyin.search.video_v2",
        http_method="POST",
        path="/api/v1/douyin/search/fetch_video_search_v2",
        sdk_resource="douyin_search",
        sdk_method="fetch_video_search_v2",
        request_style="json",
        cache_ttl_seconds=10 * 60,
    ),
    "douyin.app.multi_video_v2": EndpointSpec(
        key="douyin.app.multi_video_v2",
        http_method="POST",
        path="/api/v1/douyin/app/v3/fetch_multi_video_v2",
        sdk_resource="douyin_app_v3",
        sdk_method="fetch_multi_video_v2",
        request_style="json",
        cache_ttl_seconds=60 * 60,
        # Account usage log: one batch request cost USD 0.050 on 2026-09-20.
        # The generic calculate_price base rate is not this endpoint's tariff.
        unit_cost_usd=0.050,
        price_source="tikhub.usage_log",
        pricing_version="usage-log-2026-09-20",
    ),
    "douyin.app.user_posts": EndpointSpec(
        key="douyin.app.user_posts",
        http_method="GET",
        path="/api/v1/douyin/app/v3/fetch_user_post_videos",
        sdk_resource="douyin_app_v3",
        sdk_method="fetch_user_post_videos",
        request_style="query",
        cache_ttl_seconds=20 * 60,
    ),
    "douyin.app.user_profile": EndpointSpec(
        key="douyin.app.user_profile",
        http_method="GET",
        path="/api/v1/douyin/app/v3/handler_user_profile",
        sdk_resource="douyin_app_v3",
        sdk_method="handler_user_profile",
        request_style="query",
        cache_ttl_seconds=6 * 60 * 60,
    ),
    "douyin.app.comments": EndpointSpec(
        key="douyin.app.comments",
        http_method="GET",
        path="/api/v1/douyin/app/v3/fetch_video_comments",
        sdk_resource="douyin_app_v3",
        sdk_method="fetch_video_comments",
        request_style="query",
        cache_ttl_seconds=30 * 60,
        unit_cost_usd=0.001,
        price_source="tikhub.calculate_price",
        pricing_version="verified-2026-09-20",
    ),
    "douyin.app.comment_replies": EndpointSpec(
        key="douyin.app.comment_replies",
        http_method="GET",
        path="/api/v1/douyin/app/v3/fetch_video_comment_replies",
        sdk_resource="douyin_app_v3",
        sdk_method="fetch_video_comment_replies",
        request_style="query",
        cache_ttl_seconds=30 * 60,
    ),
    "tikhub.user.daily_usage": EndpointSpec(
        key="tikhub.user.daily_usage",
        http_method="GET",
        path="/api/v1/tikhub/user/get_user_daily_usage",
        sdk_resource="tikhub_user",
        sdk_method="get_user_daily_usage",
        request_style="none",
        cache_ttl_seconds=60,
        paid=False,
    ),
    "tikhub.user.calculate_price": EndpointSpec(
        key="tikhub.user.calculate_price",
        http_method="GET",
        path="/api/v1/tikhub/user/calculate_price",
        sdk_resource="tikhub_user",
        sdk_method="calculate_price",
        request_style="query",
        cache_ttl_seconds=6 * 60 * 60,
        paid=False,
    ),
}


# Reviewed public tariff snapshot. This is an estimate, not a reconciled bill.
# Only implemented read endpoints are added; the research inventory is not an allowlist.
for _key, _method, _name, _style, _ttl, _cost in (
    ("douyin.app.one_video", "GET", "fetch_one_video", "query", 3600, 0.001),
    ("douyin.app.multi_video", "POST", "fetch_multi_video", "json", 3600, 0.010),
    ("douyin.app.video_statistics", "GET", "fetch_video_statistics", "query", 900, 0.001),
    ("douyin.app.multi_video_statistics", "GET", "fetch_multi_video_statistics", "query", 900, 0.025),
):
    ENDPOINTS[_key] = EndpointSpec(
        key=_key, http_method=_method, path=f"/api/v1/douyin/app/v3/{_name}",
        sdk_resource="douyin_app_v3", sdk_method=_name,
        request_style=_style, cache_ttl_seconds=_ttl, unit_cost_usd=_cost,
    )

_VERIFIED_PRICES = {
    "douyin.billboard.low_fan": 0.001, "douyin.creator.material": 0.001,
    "douyin.search.video_v2": 0.010, "douyin.app.multi_video_v2": 0.050,
    "douyin.app.user_posts": 0.001, "douyin.app.comments": 0.001,
    "douyin.app.user_profile": 0.001,
    "douyin.app.comment_replies": 0.001, "douyin.app.one_video": 0.001,
    "douyin.app.multi_video": 0.010, "douyin.app.video_statistics": 0.001,
    "douyin.app.multi_video_statistics": 0.025,
    "tikhub.user.daily_usage": 0.0, "tikhub.user.calculate_price": 0.0,
}
for _key, _cost in _VERIFIED_PRICES.items():
    ENDPOINTS[_key] = replace(
        ENDPOINTS[_key], unit_cost_usd=_cost,
        price_source="tikhub.get_all_endpoints_info",
        pricing_version="public-tariff-2026-09-20",
    )


WRITE_DENY_SUBSTRINGS = (
    "/add_video_play_count",
    "/publish",
    "/send_private_message",
    "/follow",
    "/unfollow",
    "/like",
    "/unlike",
    "/delete",
    "/create_comment",
    "/reply_comment",
)


def get_endpoint(key: str) -> EndpointSpec:
    try:
        spec = ENDPOINTS[key]
    except KeyError as exc:
        raise KeyError(f"endpoint is not in V1 read allowlist: {key}") from exc
    path = spec.path.lower()
    if any(bad in path for bad in WRITE_DENY_SUBSTRINGS):
        raise ValueError(f"write-capable endpoint blocked by denylist: {spec.path}")
    return spec
