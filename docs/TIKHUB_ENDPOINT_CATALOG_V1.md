# TikHub Endpoint Catalog V1

日期：2026-09-19

2026-09-20补充：[完整价格与请求契约清单](pricing/tikhub-20260920.md)
覆盖116个业务候选/费用接口；[业务调用策略](API_BUSINESS_CALL_POLICY_V1.md)
说明单条/批量的选择、数据用途与频率。下方仍是能力目录，不代表全部已上线。

仅列当前 V1 候选接口。SDK method 以官方 Python SDK 2.1.1 reference 为准。

## Billboard

### 主链

| 目的 | SDK Method |
|---|---|
| 城市列表 | fetch_city_list |
| 垂类标签 | fetch_content_tag |
| 热门账号 | fetch_hot_account_list |
| 同城热点 | fetch_hot_city_list |
| 评论词云 | fetch_hot_comment_word_list |
| 作品趋势 | fetch_hot_item_trends_list |
| 上升热点 | fetch_hot_rise_list |
| 高涨粉率 | fetch_hot_total_high_fan_list |
| 高点赞率 | fetch_hot_total_high_like_list |
| 高完播率 | fetch_hot_total_high_play_list |
| 飙升搜索 | fetch_hot_total_high_search_list |
| 飙升话题 | fetch_hot_total_high_topic_list |
| 热门内容词 | fetch_hot_total_hot_word_list |
| 热点总榜 | fetch_hot_total_list |
| 低粉爆款 | fetch_hot_total_low_fan_list |
| 搜索热榜 | fetch_hot_total_search_list |
| 话题热榜 | fetch_hot_total_topic_list |
| 视频热榜 | fetch_hot_total_video_list |

### L3 重点账号增强

| 目的 | SDK Method |
|---|---|
| 账号粉丝趋势 | fetch_hot_account_trends_list |
| 账号近7天作品分析 | fetch_hot_account_item_analysis_list |
| 粉丝兴趣作者 | fetch_hot_account_fans_interest_account_list |
| 粉丝近3天搜索词 | fetch_hot_account_fans_interest_search_list |
| 粉丝近3天兴趣话题 | fetch_hot_account_fans_interest_topic_list |
| 粉丝画像 | fetch_hot_account_fans_portrait_list |

这些接口不进入日常全量流程。只有重点对标账号进入 L3 后按需调用，并在 V0 确认适用账号范围与真实数据覆盖。

## Creator

| 目的 | SDK Method / REST |
|---|---|
| 创作者热点 | fetch_creator_hot_spot_billboard |
| 创作者话题 | fetch_creator_hot_topic_billboard |
| 创作者热门音乐 | fetch_creator_hot_music_billboard |
| 创作者热门视频素材 | fetch_creator_material_center_billboard |
| 创作者配置 | fetch_creator_material_center_config |
| 热点/话题相关视频 | REST: /api/v1/douyin/creator/fetch_creator_material_center_related |

Creator 热门话题和热门视频素材已确认有旅行、剧情、二次元、创意、文化教育等垂类，并支持 24小时 / 7天 / 30天维度。

`fetch_creator_material_center_related` 可根据榜单返回的 `query_id` 直接取热点/话题/音乐对应的视频列表，应优先于“热点关键词再搜索视频”的自研关联方式。

注意：截至当前核验，该 related endpoint 在线 API 文档存在，但官方 Python SDK 2.1.1 仓库中未找到对应生成方法。

## App V3

| 目的 | SDK Method |
|---|---|
| 批量视频详情 | fetch_multi_video_v2 |
| 批量统计 | fetch_multi_video_statistics |
| 单视频 | fetch_one_video / v2 / v3 |
| 主页作品 | fetch_user_post_videos |
| 用户信息 | handler_user_profile |
| 评论 | fetch_video_comments |
| 评论回复 | fetch_video_comment_replies |

主页作品：
- sort_type 0 = 最新
- sort_type 1 = 最热
- count 建议 <=20
- normal 特殊情况下可测试 lite fallback

## Search

优先新 Search resource：

- fetch_user_search
- fetch_user_search_v2
- fetch_video_search_v1
- fetch_video_search_v2
- fetch_general_search_v2
- fetch_challenge_search_v2
- fetch_search_suggest

不要新增依赖已标记 deprecated 的旧 Douyin Web/App 搜索接口。

## User/Cost

- get_user_daily_usage
- calculate_price
- get_tiered_discount_info
- get_endpoint_info

多数基础服务当前基础价约 0.001 USD/request。正式报价优先读取
get_all_endpoints_info / get_endpoint_info 的 endpoint_cost 和折扣资格，
calculate_price用于规模测算，最终由账户使用日志核对。
已核验例外：`fetch_multi_video_v2` 在 2026-09-20 账户使用日志中为 0.050 USD/批次，
每批最多 50 个去重视频 ID；不得把该接口套用 0.001 USD 的通用基础价。

## REST Fallback

Provider 内统一支持：

```text
SDK method exists?
   yes -> SDK
   no  -> documented REST endpoint
```

两种通道共享：
- token
- timeout
- retries
- API budget
- cache
- raw response
- external_api_call log
- normalization

## V0 必须校验

Catalog 不是“可直接上线”名单。

只有实际 API Key 请求成功，并确认：
- 返回字段
- 唯一 ID
- 分页
- 成本
- 限流
- 数据覆盖
后，endpoint 才标记为 production-ready。
