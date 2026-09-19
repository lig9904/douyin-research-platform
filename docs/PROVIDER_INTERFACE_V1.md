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
  "signal_type": "rising_topic|hot_search|rising_search|rising_hashtag|city_hot|creator_topic|creator_music|creator_material|...",
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
- `list_signals(kind, city_code?, category?, window?, cursor?)`
- `list_creator_material(kind, category?, order?, window?)`
- `get_signal_related_videos(signal_ref, cursor?)`
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

### A. Billboard：热点/黑马发现

- `fetch_hot_total_low_fan_list`
- `fetch_hot_total_high_play_list`
- `fetch_hot_total_high_fan_list`
- `fetch_hot_total_high_like_list`
- `fetch_hot_rise_list`
- `fetch_hot_total_high_search_list`
- `fetch_hot_total_high_topic_list`
- `fetch_hot_city_list`
- `fetch_hot_total_video_list`

辅助：
- `fetch_content_tag`
- `fetch_city_list`
- `fetch_hot_comment_word_list`
- `fetch_hot_item_trends_list`

### B. Creator：创作侧热点发现

特别有价值：

- `fetch_creator_hot_spot_billboard`
- `fetch_creator_hot_topic_billboard`
- `fetch_creator_hot_music_billboard`
- `fetch_creator_material_center_billboard`

这些接口提供旅行、剧情、二次元、创意、文化教育等可直接用于本项目的垂类标签，并支持 24小时 / 7天 / 30天或热点总榜 / 同城 / 上升榜。

在线 API 文档另有：
- `fetch_creator_material_center_related`

用途：拿热点/话题/音乐的 `query_id` 后直接获取其相关视频。

这比“拿热点关键词再自行搜索视频”更准确，应优先进入 V0 实测。

### C. Index/Search：主动搜索

- structured video/content search
- user search with follower bands
- current Search V1/V2 endpoints

禁止继续选用 SDK/文档中已经标记 deprecated 的旧 Web/App 搜索方法。

### D. App V3：入选样本补详情

- `fetch_multi_video_v2`：批量视频详情
- `fetch_user_post_videos`：账号作品
  - sort_type=0 最新
  - sort_type=1 最热
  - count 建议保持 <=20
  - normal 获取不到最新时可实测 lite fallback
- `fetch_video_comments`：评论
- `fetch_video_comment_replies`：评论回复
- `handler_user_profile`：账号详情

## SDK 与 REST 双通道

TikHub 官方 Python SDK 当前仓库版本：`2.1.1`，项目状态标记为 Alpha。

V1 原则：

1. 已在 SDK 中存在且实测稳定的 endpoint → 使用 SDK。
2. 在线 API 文档已经存在、但 SDK 暂未生成的方法 → 使用统一 REST fallback。
3. REST fallback 仍必须经过同一个：
   - cache
   - budget
   - rate bucket
   - raw response persistence
   - normalization
4. 业务层不得知道某次调用来自 SDK 还是 REST。

当前已确认差异案例：

`fetch_creator_material_center_related`

在线文档存在，但当前 SDK 仓库代码搜索未发现对应方法。

因此“SDK 覆盖全部 TikHub API”不能作为系统假设；V0 必须做 SDK/API capability diff。

## Hard Rules

1. `get_videos` MUST batch up to provider maximum；TikHub App V3 优先最多 50 IDs/request。
2. Missing metrics MUST remain null；never coerce to 0。
3. Provider-specific object IDs stay in provider/raw observation tables。
4. Core business identity is Douyin account/video ID, not provider ID。
5. Pagination MUST have configured page caps。
6. Every paid call MUST pass `daily_budget` and rate-bucket guard。
7. Every request SHOULD check persisted cache before external call。
8. External raw response MUST be persistable for replay。
9. Deprecated endpoints MUST NOT be selected when an official replacement is available。
10. SDK version is pinned；upgrade only after regression tests。
11. SDK absence MUST NOT block a documented endpoint：use controlled REST fallback。
12. Agent cannot control raw pagination freely。

## L0/L1 Allowed Provider Calls

Allowed:
- Billboard
- Creator material/hotspot billboards
- Index/Search
- signal → related video first page
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

TikHub Python SDK:
https://github.com/TikHub/TikHub-API-Python-SDK

SDK reference:
https://github.com/TikHub/TikHub-API-Python-SDK/blob/main/docs/reference.md

Creator related videos:
https://docs.tikhub.io/452620367e0

App V3 user posts:
https://docs.tikhub.io/186826223e0
