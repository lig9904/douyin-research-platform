# 数据来源、保留与可清理设计

日期：2026-09-19

## 1. 为什么现在就要设计

研究平台要求长期历史回看，但第三方 API 的服务条款、平台政策和接口能力可能变化。

TikHub 2026-03-17 现行 Terms 明确说明：
- 平台数据能力可能因原平台政策、技术或法律要求变化
- 用户应自行负责下游数据使用合规
- 服务终止后，条款要求停止使用服务并删除通过服务取得的缓存或存储数据

因此数据库不能假设“任何第三方原始数据都可以永久无条件保存”。

## 2. 技术原则

每一条外部数据必须能回答：
- 来自哪个 provider
- 哪个 endpoint / source family
- 何时取得
- 原平台实体 ID
- 是否是 raw / normalized / derived
- 被哪些分析引用
- 是否允许单独删除

## 3. 数据分层

### A. Raw provider data
例如 TikHub raw_payload、原始评论、原始画像。
必须明确 provider，并可按 provider 删除。

### B. Normalized public metrics
例如 aweme_id、点赞/评论/播放快照、作者 ID。
必须保留 provenance。

### C. Our annotations
例如人工标签、收藏、内部备注、研究状态。
逻辑上与 provider raw 数据分离。

### D. Derived analysis
例如黑马 score、Pattern candidate、AI analysis。
必须保存 input_refs / source lineage。

如果底层 provider 数据需要删除，系统能够识别哪些分析依赖该数据，并按届时适用条款/内部政策决定保留、重算或删除。这里不预先作法律结论。

## 4. V1 Schema 要求

现有 provider 字段继续保留。

V0 后 migration 应补充：
- source_family
- source_endpoint_key
- provider_request_id
- provenance JSON / relation
- raw_data_retention_class
- source_deleted_at
- source_unavailable_at

评论等目前缺 provider 的表需补齐。

## 5. Purge 能力

后续至少提供管理员脚本：
- dry_run_provider_purge(provider, before_date)
- list_provider_dependencies(provider)
- purge_raw_provider_data(...)
- mark_source_unavailable(...)

默认先 dry-run，输出：
- 将删除多少 raw rows
- 影响多少 normalized entities
- 哪些 analysis_run 引用了这些数据
- 哪些人工标注与其关联

## 6. Provider Cache 不能替代我们的 Freshness

TikHub 返回结构普遍包含 cache_message/cache_url，Privacy Policy 也说明 TikHub 可能缓存公开平台数据。

但目前没有找到统一适用于所有 Douyin endpoint 的缓存 TTL 保证。

所以自己的 freshness 仍由：
- captured_at
- endpoint policy
- local cache TTL
- metric_snapshot
控制。

## 7. TikHub 数据/隐私边界

TikHub Privacy Policy 当前说明其可能缓存公开平台数据，并将聚合/去标识后的 Raw Platform Data 用于第三方许可等用途，同时允许客户联系其 opt out。

因此：
- V1 只传必要公开数据查询参数
- 不向 TikHub 发送我们的内部研究文档
- Creator Cookie 等高敏感凭据不默认接入
- 自有账号数据优先使用抖音官方授权 OpenAPI

## 8. Windmill 许可证边界

Windmill Community Edition 官方说明为 AGPLv3。

V1 不修改 Windmill 本体，只运行官方 Community 镜像。

我们的 scripts、flows、Full-code App、prompts、rules、SQL 全部保存在自己仓库。

若未来要 fork/修改 Windmill 核心，再单独做许可证评估。
