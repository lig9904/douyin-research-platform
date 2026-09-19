# Provider Interface V1

日期：2026-09-19

## 目标

业务层只依赖统一 Provider Contract，不直接依赖 TikHub 字段和方法名。

V1 provider 名称：`tikhub`

## 统一实体

### Account

```json
{
  "platform": "douyin",
  "platform_account_id": "sec_user_id or stable platform id",
  "nickname": "...",
  "profile_url": "...",
  "metrics": {
    "follower_count": null,
    "following_count": null,
    "total_favorited": null,
    "video_count": null
  },
  "raw": {}
}
```

### Video

```json
{
  "platform": "douyin",
  "platform_video_id": "aweme_id",
  "platform_account_id": "...",
  "title": "...",
  "description": "...",
  "source_url": "...",
  "published_at": null,
  "duration_ms": null,
  "availability_status": "available",
  "metrics": {
    "play_count": null,
    "like_count": null,
    "comment_count": null,
    "share_count": null,
    "collect_count": null
  },
  "raw": {}
}
```

### Signal

```json
{
  "platform": "douyin",
  "signal_type": "rising_topic|hot_search|rising_search|rising_hashtag|city_hot|creator_topic|...",
  "provider_signal_id": null,
  "signal_key": "...",
  "title": "...",
  "category_key": null,
  "city_code": null,
  "snapshot": {
    "rank_value": null,
    "rank_change": null,
    "heat_value": null
  },
  "raw": {}
}
```

## Provider 方法

### Discovery

- `list_video_billboard(kind, window, keyword?, category?, cursor?)`
- `list_signals(kind, city_code?, cursor?)`
- `search_videos(query, filters, cursor?)`
- `search_accounts(query, follower_band?, cursor?)`

### Enrichment

- `get_videos(ids[])`
- `get_account(account_id)`
- `get_account_videos(account_id, sort, cursor?)`
- `get_comments(video_id, cursor?)`
- `get_comment_word_cloud(video_id)`
- `get_video_trend(video_id, metric, granularity)`

### Meta / Cost

- `get_filter_options()`
- `get_endpoint_cost(endpoint_key, planned_requests?)`
- `get_daily_usage()`

## TikHub Mapping

### Discovery

Billboard:
- low-follower viral
- high completion
- high follower growth
- high likes
- rising hot
- rising search
- rising topic
- city hot / content tags where useful

Index/Search:
- structured content search
- user search with follower bands

### Enrichment

App V3:
- `fetch_multi_video_v2` preferred for batch video details
- `fetch_user_post_videos` for account videos (sort_type 0 latest / 1 hottest)
- `fetch_video_comments` for comments
- App V3 endpoints preferred over deprecated Web search/profile endpoints

## Hard Rules

1. `get_videos` MUST batch up to provider maximum; for TikHub App V3 use up to 50 IDs/request.
2. Missing metrics MUST remain null; never coerce to 0.
3. Provider-specific object IDs stay in raw/provider snapshot tables.
4. Core business identity is Douyin account/video ID, not provider ID.
5. Pagination MUST have configured page caps.
6. Every paid call MUST pass `daily_budget` and rate-bucket guard.
7. Every request SHOULD check persisted cache before external call.
8. External raw response MUST be persistable for replay.
9. Deprecated endpoints MUST NOT be selected when an official replacement is available.
10. SDK version is pinned and upgraded only after V0/V1 regression tests.

## L0/L1 Allowed Provider Calls

Allowed:
- Billboard
- Index/search
- batch video detail
- basic account metadata when needed

Disallowed by default:
- all-pages comments
- HQ media
- expensive KOL/Xingtu endpoints
- uncontrolled account-history pagination
- LLM

## L2/L3

L2 can request:
- comment word cloud
- first/top comment page
- transcript/media only when required

L3 can request:
- additional comment pages under cap
- account context
- trend details
- high-cost model analysis

## References

TikHub Python SDK reference:
https://github.com/TikHub/TikHub-API-Python-SDK/blob/main/docs/reference.md

TikHub App V3 user posts:
https://docs.tikhub.io/186826223e0
