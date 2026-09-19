# V1 实施蓝图

日期：2026-09-19

## 1. 目标

V1 只做：

- 现成接口采集
- 代码/规则粗筛
- 分层精研
- 历史回看
- Web 研究台
- 搜索/筛选
- Codex MCP 访问
- 成本与任务可观测

不做：

- 自研抖音爬虫
- NAS 媒体归档
- LibTV 自动生产
- 自研独立 API Server
- 自研独立 MCP Server
- Redis / Kafka / Kubernetes
- 默认全量评论 / 全量 ASR / 全量视频下载

---

## 2. 最终 V1 栈

```text
Douyin
  ↓
TikHub API
  ↓
TikHub official Python SDK
  ↓
Windmill scripts / flows
  ↓
Provider normalization
  ↓
PostgreSQL (douyin_research)
  ├─ raw payload
  ├─ snapshots
  ├─ discovery
  ├─ scores
  ├─ transcripts
  ├─ analyses
  └─ annotations
  ↓
Windmill Full-code React App
  ├─ 今日发现
  ├─ 黑马视频
  ├─ 对标账号
  ├─ 历史案例
  ├─ 搜索
  └─ Case 详情
  ↓
Windmill MCP
  ↓
Codex
```

Windmill 自身使用独立 `windmill` 数据库。

---

## 3. TikHub 使用策略

### L0 / 发现

优先：

- Douyin Billboard
- Douyin Index

用途：

- 热点
- 上升热点
- 低粉爆款
- 高完播
- 高涨粉
- 高点赞
- 关键词/垂类搜索

### L1 / 代码粗筛

不调用 LLM。

使用：

- 账号粉丝量
- 播放 / 点赞 / 评论 / 分享
- 发布时间
- 榜单命中
- 账号历史基线
- 多时间快照
- 自定义规则版本

### L2 / 补上下文

优先：

- App V3 batch video detail
- 少量评论
- 评论词云（若 V0 实测有效）
- ASR（按需）

### L3 / 深研

极少量高价值 Case 才调用强模型。

---

## 4. TikHub Provider 接口边界

业务代码不能暴露 TikHub 原始字段。

内部接口预期：

```python
class DouyinProvider:
    def fetch_discovery_lists(...)
    def search_videos(...)
    def search_accounts(...)
    def fetch_account(...)
    def fetch_account_videos(...)
    def fetch_video_batch(...)
    def fetch_comments(...)
    def fetch_comment_wordcloud(...)
    def fetch_usage(...)
    def calculate_price(...)
```

TikHub 是第一个实现：

```text
TikHubDouyinProvider
```

以后可以增加：

```text
AnotherDouyinProvider
```

业务逻辑不改。

---

## 5. API 成本控制

### 5.1 批量详情

候选视频 ID 按最多 50 个组成批次，优先调用：

`/api/v1/douyin/app/v3/fetch_multi_video_v2`

不要逐视频调详情。

### 5.2 分页上限

所有收费分页必须显式设置：

- max_pages
- max_items
- max_cost

任何 Agent / Flow 不允许无限翻页。

### 5.3 全局 rate / budget

Windmill CE 不提供全局 Concurrency Limits。

自己实现：

```text
api_rate_bucket
api_daily_budget
```

通过 PostgreSQL transaction + row lock / token bucket 保证多 worker 共享限制。

### 5.4 对账

每天：

```text
external_api_call
        ↕
TikHub get_user_daily_usage
```

发现偏差立即报警。

---

## 6. Windmill 使用策略

直接使用现成能力：

- Docker Compose self-host
- Scheduler
- Worker
- Job queue
- Flow
- Retries
- Error handlers
- Cache
- Resources / Secrets
- Full-code App
- MCP Gateway
- Version history

不自研这些能力。

---

## 7. Full-code App

框架：

```text
React 19
```

生成：

```bash
wmill app new
wmill app dev
wmill app generate-agents
wmill generate-metadata
```

前端通过自动生成的：

```text
wmill.ts
```

调用 backend runnables。

backend 优先：

- 查询类：`.pg.sql`
- 简单业务逻辑：TypeScript/Bun
- TikHub SDK / 数据处理：Python

---

## 8. 用户访问

V1 不优先使用 Guest。

原因：

- Guest 需要 SSO/OAuth 或自建 JWT issuer
- 会额外增加身份系统工作量

V1 优先：

- 手工创建 Windmill 用户
- 普通查看人员设为 Operator
- 开发人员设为 Developer
- 用户只获得研究 App 所需权限

如果以后人数超过 Community Edition 合理范围，再重新评估 Guest / SSO / 独立前端。

---

## 9. App 执行身份

Windmill App 默认使用 publisher 权限运行 backend runnable。

建议：

- 使用一个权限受限的专用普通用户作为 App publisher
- 不使用 superadmin 发布研究台
- publisher 只拥有：
  - douyin_research DB resource
  - 本项目 scripts / flows
  - 必需 secrets

人工行为记录：

```python
actor = os.environ.get("WM_END_USER_EMAIL") or os.environ.get("WM_EMAIL")
```

不要直接用 `WM_EMAIL`。

---

## 10. Git / Codex 工作流

GitHub 是 source of truth。

```text
GitHub repo
  ↓
Codex
  ↓
wmill CLI
  ↓
Windmill workspace
```

初始化：

```bash
wmill init
wmill app new
wmill app generate-agents
wmill generate-metadata
```

同步：

```bash
wmill sync pull
wmill sync push
```

注意：

- `skipSecrets: true`
- API key 不进入 Git
- `.env` 不进入 Git
- secrets 只放 Windmill Resource / Secret

---

## 11. 数据库访问

研究 App 不直接在前端连接 PostgreSQL。

所有查询走 backend runnable。

查询优先使用原生 `.pg.sql`：

- 参数化
- 简单
- 不引入 ORM
- 不增加 API server

复杂逻辑再用 Python / TypeScript。

---

## 12. 搜索

### V1

先用：

- ID
- 作者
- 时间
- 标签
- 状态
- ILIKE / substring
- 结构化过滤

### V1.x

当数据量与体验证明需要时：

- PGroonga

不要一开始上 Meilisearch。

---

## 13. Windmill Cache 用途

适合短 TTL：

- filter options
- content tags
- 热榜短周期重复请求
- 稳定账号基础资料

长期历史仍进入 PostgreSQL。

---

## 14. TikHub 域名

以最新官方 API 文档为准：

```text
https://api.tikhub.io
```

为主域名。

官方最新文档明确提示避免优先使用 `api.tikhub.dev`，其性能可能更慢。

如果出现 >30 秒请求，再单独解决网络/proxy，而不是默认改域名。

---

## 15. 第一版目录预期

```text
f/douyin_research/
├── collectors/
├── provider/
├── scoring/
├── analysis/
├── search/
├── reports/
├── flows/
└── research_app.raw_app/
    ├── App.tsx
    ├── index.tsx
    ├── index.css
    ├── package.json
    ├── raw_app.yaml
    └── backend/
        ├── get_today.pg.sql
        ├── search_cases.pg.sql
        ├── get_case.pg.sql
        ├── save_annotation.ts
        └── ...
```

---

## 16. 开发顺序

### Step 0

TikHub API Key 实测。

### Step 1

Windmill + PostgreSQL Compose 起环境。

### Step 2

`wmill init` + 生成官方 agent context。

### Step 3

实现 TikHub Provider。

### Step 4

L0/L1 全链路，不使用 LLM。

### Step 5

Full-code React 研究台。

### Step 6

Windmill MCP → Codex。

### Step 7

L2/L3 分层精研。

---

## 17. V1 成功标准

不是“功能很多”，而是：

1. 每天能稳定自动发现候选。
2. 大多数候选只付很低 API 成本。
3. 不使用 LLM 也能完成大部分过滤。
4. 团队任何普通成员能打开页面看懂。
5. 能搜到过去研究过的案例。
6. 能看到当时为何进入候选。
7. Codex 可通过 MCP 查相同数据。
8. 所有外部收费调用可追踪、可限制、可对账。
