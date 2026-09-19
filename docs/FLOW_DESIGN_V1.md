
# V1 Windmill Flow 设计

日期：2026-09-19

## 1. Trigger Script 的正确定位

TikHub 当前未发现可替代轮询的抖音热点/账号 webhook。Windmill Trigger Script 正适合这种场景：定时拉外部源、使用 State 保存上次小型状态、返回新增项；无新增项时后续 Flow 可跳过。

State 只保存小型瞬态信息：
- last_poll_at
- recent_ids
- last_cursor
- last_hash

PostgreSQL 保存长期事实：
- discovery
- raw payload
- metric snapshot
- score
- analysis
- annotation

## 2. 防止丢事件

不要先推进 State 再在后续步骤落库。

推荐顺序：
poll external
-> normalize
-> PostgreSQL 幂等 upsert / discovery insert
-> 得到 truly_new_ids
-> 更新 Trigger State
-> return truly_new_ids
-> 后续 Flow

数据库唯一约束是最终幂等依据，State 只是优化器。

## 3. Flow A：热点/榜单发现

schedule
-> trigger_poll_billboards.py
-> 调配置好的 Billboard feeds
-> normalize
-> raw payload + upsert video/account + discovery
-> return new/changed IDs
-> 空则 early return
-> L1 纯代码评分
-> threshold branch
-> promote / observe / reject
-> 需要时进入 batch detail 队列

采用配置驱动，不为每个榜单复制代码。

## 4. Flow B：关键词 / Index 发现

与实时热点分开，因为关键词池和研究 Profile 更新较慢。

schedule/manual
-> load enabled research_queries
-> bounded loop
-> TikHub Search / Index
-> normalize + dedupe
-> PostgreSQL
-> L1 scoring

每个 query 保存：
- max_pages
- max_items
- max_cost_usd
- schedule
- enabled
- purpose/profile

## 5. Flow C：对标账号监控

select due accounts
-> bounded loop
-> fetch first page of posts
-> compare IDs with DB
-> only new posts
-> store
-> L1 scoring

规则：
- 默认只第一页
- 没有新视频就停止
- 发现新视频后才补详情
- A/B/C 账号不同频率

## 6. Flow D：视频指标 Snapshot

SQL select due videos
-> chunk IDs by 50
-> App V3 fetch_multi_video_v2
-> metric_snapshot
-> SQL/code calculate deltas
-> update next_due_at / priority

动态频率：
- 快速增长：更快下一次
- 增长趋平：自动降频
- >7d 且非重点：停止

全程不用 LLM。

## 7. Flow E：L2 Enrichment

只消费晋级样本：
- 评论词云（若 V0 验证可用）
- 热门评论第一页
- transcript/ASR（按需）
- account baseline

每一步都：
cache first
-> freshness check
-> budget check
-> only fetch missing/stale data

随后用确定性特征完成 L2 score。

## 8. Flow F：L3 Deep Research

少量 Case：
evidence package
-> LLM structured analysis
-> JSON Schema validation
-> analysis_run
-> optional human review

LLM 不直接访问 TikHub。输入只能来自已经受预算控制并落库的数据。

## 9. Flow G：成本对账

每天一次：
local external_api_call sum
-> TikHub get_user_daily_usage
-> compare
-> store reconciliation
-> 超阈值报警

## 10. Flow H：数据质量检查

每日检查：
- feed 成功率
- 新视频数量异常
- 429 / 5xx
- 空响应比例
- raw payload schema drift
- duplicate ratio
- snapshot stale count
- L2/L3 failure
- local cost vs provider usage

## 11. Retry 分层

TikHub SDK 当前默认 max_retries=3，并暴露 TikHubRateLimitError.retry_after。Windmill Flow 也支持 step/flow retry。

不能两层都设置大量 retry，否则一个故障可能形成乘法重试。

V0 应故障实测后采用：
- SDK：少量短周期 HTTP retry
- Windmill：较长间隔 job retry
- 全局 PostgreSQL rate bucket 仍优先于 SDK retry

## 12. 并发

本地 SQL/评分可高并发。

所有收费 TikHub 调用先经过统一 acquire_api_token(provider, endpoint)，使用 PostgreSQL 共享令牌桶/预算，避免多个 Worker 同时打爆外部 API。

## 13. MCP 边界

Codex 暴露业务语义工具：
- search_cases
- get_case
- get_blackhorse_candidates
- get_account_history
- request_deep_analysis

不暴露：
- arbitrary_tikhub_call
- raw_paid_endpoint
- unlimited_fetch_next_page

这样从工具层面阻止 Agent 无界花钱。

## 14. 后续 Schema 需要补的能力

V0 后再迁移，不现在贸然改：
- next_due_at
- monitoring_priority
- cache/freshness
- request fingerprint/idempotency
- discovery observation key
- API budget/rate bucket
- cost reconciliation
