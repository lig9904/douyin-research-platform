# Research Provider 只读策略

日期：2026-09-19

## 1. 目的

TikHub SDK 同时包含读取数据和改变平台状态的接口。

研究平台只需要观察和分析，不应产生任何平台互动。

因此 Provider 采用“只读 allowlist”，而不是“整个 SDK 都可调用”。

## 2. 明确禁止进入 Provider 的能力

包括但不限于：

- 增加视频播放数
- 点赞/取消点赞
- 关注/取消关注
- 发布内容
- 评论/回复
- 私信
- 收藏/取消收藏
- DOU+ 投放
- 任何自动互动
- 任何需要模拟用户行为的写操作

即使 SDK 存在这些 method，也不得：
- 封装
- 暴露为 Windmill script
- 暴露为 MCP tool
- 暴露给 LLM
- 在测试中对真实抖音目标调用

## 3. Provider Allowlist

只允许显式登记的读取类业务能力，例如：

- fetch_discovery_lists
- fetch_hot_topics
- search_videos
- search_accounts
- fetch_account
- fetch_account_videos
- fetch_video_batch
- fetch_comments
- fetch_comment_wordcloud
- fetch_index_trends
- fetch_index_portrait
- fetch_usage
- calculate_price

新的 endpoint 需要代码 review 后才加入 allowlist。

## 4. 不提供 generic passthrough

禁止这种设计：

```python
provider.call(endpoint, params)
```

因为它等于重新向 Agent 打开整个 TikHub API。

必须是：
```python
provider.fetch_video_batch(ids)
provider.fetch_hot_topics(...)
```

业务语义明确、参数有硬限制。

## 5. MCP 再缩一层

Codex 看到的工具应该比 Provider 更少：

- search_cases
- get_case
- get_trends
- get_blackhorse_candidates
- get_account_history
- request_refresh
- request_deep_analysis

Codex 默认不直接调用 TikHub。

## 6. 测试保护

自动测试：
- 扫描 Provider source 中是否出现 denylisted SDK method
- 检查所有外部 endpoint 是否属于 read allowlist
- test 环境默认只允许 TikHub Demo endpoint
- 真正付费/真实抖音 endpoint 必须显式 integration marker + secret

## 7. 为什么需要

除了安全和研究纯度，TikHub 当前 Terms 也明确禁止 fake engagement。

我们的平台是 Research Platform，不是自动互动 Bot。
