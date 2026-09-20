# Douyin Provider Contract V1

日期：2026-09-19

> 目的：让业务层永远依赖“我们的抖音研究语义”，而不是 TikHub 字段。TikHub 只是第一个 Provider。

## 1. 总原则

- read-only allowlist
- no generic endpoint passthrough
- bounded pagination
- explicit cost budget
- explicit freshness
- raw payload separated from normalized entities
- null != zero
- unavailable != error
- provider provenance always retained

## 2. 通用值语义

任何不是必然存在的外部指标都不能简单定义为 number。

逻辑上使用：

```text
MetricValue<T>
- status: available | unavailable | not_applicable | error
- value: T | null
- observed_at
- provider
- source_endpoint
```

数据库中可以用 NULL + status/source metadata实现，不必真的嵌套对象。

绝不：
- 接口没返回播放量 -> 写 0
- 私密视频 -> 当成 0 播放
- 请求失败 -> 覆盖旧值为 NULL

## 3. 最小实体

### AccountRef

```text
provider
platform = douyin
platform_account_id
sec_user_id?           # 若provider提供
nickname?
profile_url?
follower_count?
observed_at
raw_ref?
```

### VideoRef

```text
provider
platform = douyin
platform_video_id
account_platform_id?
title?
description?
source_url?
published_at?
duration_ms?
availability_status
observed_at
```

availability_status 统一枚举候选：
- available
- deleted
- private
- restricted
- unavailable
- unknown

实际映射由 V0 冻结。

### MetricSnapshotInput

```text
video_platform_id
captured_at
play_count?
like_count?
comment_count?
share_count?
collect_count?
author_follower_count?
metric_status{}
provider
source_endpoint
raw_ref
```

### DiscoverySignal

```text
signal_type
signal_key
video_platform_id?
account_platform_id?
topic_id?
query_text?
rank?
score?
window?
category?
city?
observed_at
provider
source_endpoint
metadata
```

signal_type 例如：
- low_fan_hit
- high_completion
- high_growth
- high_like
- rising_hot
- rising_search
- rising_topic
- creator_hot_video
- city_hot
- manual_submission

### CommentSample

```text
platform_comment_id
video_platform_id
parent_platform_comment_id?
text
like_count?
published_at?
reply_count?
sample_reason
observed_at
provider
source_endpoint
raw_ref?
```

sample_reason:
- top
- recent
- keyword
- thread_root
- manual

### StructuredIntelligence

```text
intelligence_type
scope_type
scope_key
period_start?
period_end?
region?
category?
normalized_metrics
captured_at
provider
source_endpoint
raw_ref
```

用于：
- keyword_trend
- keyword_interpretation
- related_words
- audience_portrait
- category_duration_distribution
- category_consume_trend
- creative_topics
- account_trend
- video_index_trend

### ProviderCallMeta

```text
provider
endpoint_key
request_fingerprint
started_at
finished_at
status
http_status?
cached
request_count
estimated_cost?
currency
provider_request_id?
retry_count
metadata
```

## 4. Provider 方法

概念接口：

```python
class DouyinResearchProvider:

    # discovery
    fetch_billboard(signal, *, window, category=None, keyword=None, limit=None)
    fetch_creator_discovery(kind, *, category=None, city=None, period=None)

    # search
    search_videos(query, *, filters, page_token=None, max_items=None, max_cost=None)
    search_accounts(query, *, follower_band=None, page_token=None, max_items=None, max_cost=None)

    # details
    fetch_videos(video_ids)          # internally batches
    fetch_account(account_id)
    fetch_account_posts(account_id, *, channel="normal", page_token=None, max_items=None)

    # comments
    fetch_comment_wordcloud(video_id)
    fetch_comments(video_id, *, page_token=None, max_items=None, max_pages=None, max_cost=None)

    # intelligence
    fetch_keyword_trends(...)
    fetch_related_words(...)
    fetch_audience_portrait(...)
    fetch_category_intelligence(...)
    fetch_video_intelligence(...)
    fetch_account_intelligence(...)

    # billing
    calculate_price(endpoint_key, requests_per_day)
    fetch_daily_usage(...)
```

所有 list/search 方法必须有硬边界。

## 5. Provider 返回不得暴露的东西

业务层不得依赖：
- TikHub特有 response envelope
- TikHub status_code/status_msg
- TikHub cache_url
- 某个endpoint私有字段层级
- Web/App V3不同内部命名

这些只保存在 raw payload / provider adapter。

## 6. Provider 错误分类

统一成：

- ProviderAuthError
- ProviderBalanceError
- ProviderRateLimitError(retry_after?)
- ProviderTemporaryError
- ProviderPermanentError
- ProviderNotFound
- ProviderRestricted
- ProviderSchemaError

Windmill Flow 只依赖这些统一错误。

## 7. Freshness

每一种业务能力有自己的 TTL，不由 TikHub cache_message决定。

例如候选：
- billboard: 15–30m
- account profile: 6–24h
- old video metadata: 24h+
- filter options: 24h+
- structured portraits: 1d/1w，按官方数据周期

Provider 调用前由业务 cache policy 判断是否需刷新。

## 8. Cost Contract

每个外部调用都必须先拥有：

```text
CallBudget:
- priority
- max_requests
- max_cost
- deadline?
```

分页方法每次准备翻页前重新检查预算。

Provider 不允许自己无限追 has_more。

## 9. Idempotency / Fingerprint

request_fingerprint 至少由：
- provider
- endpoint_key
- normalized params
- page token/cursor
- auth scope（不含secret本身）

组成。

用于：
- 短时缓存
- 调用审计
- 防重复收费
- 调试

## 10. Read-only 编译/测试防线

维护：
- READ_ALLOWLIST
- WRITE_DENYLIST

CI 检查 provider 代码中是否引用已知写接口名。

任何新增 SDK method 必须：
1. 明确业务用途
2. 确认只读
3. 在V0/后续接口验证文档记录
4. code review
5. 才进入 allowlist

## 11. TikHub Adapter 特殊策略

### 视频详情
当前适配器仍自动 chunk <= 50。2026-09-20重新核价后，目标策略改为按缺失字段、
有效ID数量和折扣选择单条/10批/50批；纯增长指标优先验证统计接口。
详见 [业务接口调用策略](API_BUSINESS_CALL_POLICY_V1.md)，新路由尚未部署。

### 账号作品
默认 normal；仅重点账号、必要时一次 lite fallback。

### 评论
默认一页/有限采样；count 保持 1–20 的硬边界。

2026-09-19 真实接口验证确认：
- cursor 可以推进，但相邻评论页可能重叠，业务层不得假设分页互斥
- Provider 必须按稳定 comment ID 跨页去重
- `max_pages` 与 `max_items` 必须同时限制；cursor 缺失、不变或 `has_more=0` 时立即停止
- 每次缓存未命中、准备发起外部请求前重新执行调用/预算闸门
- 回复默认单页读取，只有显式上层预算与页数限制后才允许继续翻页
- canonical comment 与 observation 分表：同一 raw response 重放不得新增 observation，新 raw response 才形成新观察
- 缓存命中必须沿用原始 raw_ref，不能伪造为一次新观察

### Index
filter/category等枚举动态拉取并缓存，不硬编码长期枚举。

### Creator V2
不属于 V1 public-data Provider。未来自有账号连接单独实现，且官方 Douyin OpenAPI 优先。

## 12. V0 后冻结范围

V0 只冻结：
- 最小内部字段
- ID映射
-错误语义
- pagination abstraction
- cost/freshness contract

不要把 TikHub 全部 1000+ endpoint “支持一遍”。只封装 V1 真正需要的能力。
