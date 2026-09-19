# 可直接复用的现成能力

## TikHub

### 直接使用

- Official Python SDK
- Billboard
- Index search
- App V3
- batch video detail
- user daily usage
- calculate price
- Hosted MCP / Plugin（仅作为开发研究辅助）

### 借鉴而不直接作为生产主链

TikHub Plugin 现成 skills：

- trend-research
- competitor-analysis
- creator-analytics
- comments-analysis
- bulk-data-export

其中最值得直接借鉴的设计原则：

- 大批量任务先估成本
- 每个分页任务设置 cap
- 去重后再分析
- 对 Agent 暴露目标级能力，而不是任意底层 endpoint

---

## Windmill

### 直接使用

- 官方 docker-compose
- Postgres + server + worker 架构
- Full-code App scaffold
- React 19
- backend runnables
- native PostgreSQL scripts
- Scheduler
- Flow
- retries / exponential backoff
- error handler
- cache
- Secrets / Resources
- MCP Gateway
- wmill CLI
- agent context generation
- version history

### Windmill Hub

可优先复用 verified building blocks，例如：

- PostgreSQL execute query
- PostgreSQL TLS
- Postgres backup to S3
- Slack / Discord error handler

但 Hub 资产进入生产前必须人工审核，不自动拉入。

---

## PostgreSQL

### 直接使用

- JSONB raw payload
- B-tree indexes
- GIN JSONB（仅实际查询需要时）
- 普通 snapshot 表
- row lock / transaction 做全局 rate/budget
- ILIKE / substring V1 搜索

### 后置

- pg_trgm：只作为补充，不作为中文全文检索核心
- PGroonga：历史数据规模上来后再评估
- partitioning：实际表规模证明需要后再做

---

## 明确不引入

V1 不引入：

- Appsmith
- Supabase
- Meilisearch
- Redis
- Kafka
- RabbitMQ
- Kubernetes
- 独立 MCP server
- 独立 API server
- 独立 scheduler
- 自研 worker queue
