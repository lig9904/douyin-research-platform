# V1 成本控制策略

## 1. 原则

成本优先级：

1. 不调用
2. 缓存
3. 批量接口
4. 代码/SQL
5. 低成本数据补充
6. ASR/评论
7. 大模型深研

## 2. TikHub

大多数基础接口官方基础价约 0.001 USD / request，并采用每日请求量阶梯折扣。

系统不硬编码价格，使用 TikHub：
- get_endpoint_info
- calculate_price
- get_user_daily_usage

定期同步真实计费信息。

## 3. 批量优先

视频详情优先 App V3 fetch_multi_video_v2：
- 最多 50 IDs / request
- 固定 0.001 USD / request

禁止在批量接口可覆盖时循环调用单条详情。

## 4. L0

只使用：
- Billboard
- Index
- 已缓存筛选项

不使用：
- 评论
- ASR
- 视频下载
- LLM

## 5. L1

只用：
- SQL
- Python/Go
- metric snapshots
- account baseline
- deterministic scoring

不使用 LLM。

## 6. L2

只有晋级样本：
- 评论词云（实测可用性后）
- Top page 评论
- Transcript / ASR（确有必要时）
- embedding / clustering（确有必要时）

## 7. L3

仅 Top-N Case：
- 强模型
- 多案例比较
- 机制假设
- IP适配

## 8. API Guard

Community Windmill 没有免费全局 concurrency limit。

由 PostgreSQL：
- api_rate_bucket
- daily_budget

统一控制多 worker 的 API 预算。

## 9. 外部 API 日志

每次外部请求记录：
- endpoint
- fingerprint
- cached
- http status
- estimated cost
- actual cost（可回填）
- started/finished
- metadata

## 10. Agent 边界

Codex MCP 默认只读已入库数据。

会产生新收费请求的工具必须：
- 名称明显
- 先查缓存
- 经过 daily_budget
- 限制翻页
- 限制 Top-N
- 返回本次预计成本/调用数
