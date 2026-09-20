# 实施级深挖报告：TikHub + Windmill + PostgreSQL

日期：2026-09-19

> 2026-09-20更正：本报告保留为历史研究。下文“批量详情固定0.001”及相关成本推导
> 已失效，当前为USD0.050/50条整批；单/批选择和价格以
> [业务调用策略](API_BUSINESS_CALL_POLICY_V1.md) 与 [当前价表](pricing/tikhub-20260920.md) 为准。

> 目的：只研究 V1 已决定采用的组件，确认真实能力、限制、成本与实现方式，避免重复开发。

## 结论摘要

V1 继续采用：

- TikHub：抖音第三方数据源
- Windmill Community Edition：脚本、Flow、调度、Worker、Secrets、Full-code App、MCP Gateway
- PostgreSQL：业务事实库
- GitHub：源码与任务管理

但有 8 个重要修正：

1. TikHub 不应被当成一个单一 API；应按 Billboard / Index / App V3 三类接口分工。
2. 常规开发优先使用 TikHub 官方 Python SDK，不让 Codex 直接无限调用 TikHub Hosted MCP。
3. Windmill 使用 Full-code App；Low-code App 已被官方标记为 legacy。
4. Windmill CE 的“Concurrency limits”是 Enterprise 功能，V1 必须自己用 PostgreSQL/代码实现全局 API 限频与预算闸门。
5. Windmill CE 自带结果缓存，可直接用于短时 API 去重，但持久缓存与审计仍写业务库。
6. Windmill CE 内置 Git Sync 仅在 workspace <= 2 users 时完整可用；多人使用后不应依赖它。源码以 GitHub 为准，用 wmill CLI / CI 做 sync push。
7. 中文历史全文搜索第一版不要押注 PostgreSQL 原生 FTS；pg_trgm 对中文有 locale/实现限制。V1 用结构化筛选 + ILIKE；真正需要中文全文检索时优先评估 PGroonga。
8. Windmill App 后端默认以 publisher 身份执行，但可通过 WM_END_USER_EMAIL 获取实际访问者身份，因此人工标注可以正确记录操作人。

---

# 一、TikHub

## 1. 推荐接入方式

生产采集优先：

```text
Windmill Python Script
        ↓
TikHub official Python SDK
        ↓
Provider normalization
        ↓
PostgreSQL
```

官方 Python SDK 当前说明：

- 覆盖 OpenAPI 1010/1010 endpoints
- Python 3.9+
- sync + async
- automatic retries
- exponential backoff
- rate-limit handling
- httpx + pydantic v2
- Douyin 400+ endpoints

建议依赖版本先固定在已验证版本范围，而不是无上限升级。

官方：
- https://github.com/TikHub/TikHub-API-Python-SDK
- https://github.com/TikHub/tikhub-plugin

## 2. 三类接口分工

### A. Douyin Billboard：发现层

适合 L0/L1：

- 视频总榜
- 低粉爆款
- 高完播率
- 高涨粉率
- 高点赞率
- 上升热点
- 热搜
- 上升搜索
- 上升话题
- 热门账号
- 评论词云
- 作品趋势

视频榜支持：

- 1 / 24 / 72 / 168 小时时间窗口
- keyword
- 垂类 tags

这是“系统主动发现候选”的首选。

官方：
- https://docs.tikhub.io/252393853e0
- https://github.com/TikHub/TikHub-API-Python-SDK/blob/main/docs/reference.md

### B. Douyin Index：结构化搜索层

`fetch_item_query` 可按：

- keyword
- category
- 发布时间
- 时长
- 低粉爆款 / 高完播 / 高涨粉 / 高点赞

搜索结果本身包含：

- 播放
- 点赞
- 评论
- 分享
- 作者
- 作者粉丝数

非常适合：

- 主动建立对标池
- 垂类搜索
- 针对某个 Pattern 找样本

筛选值应动态读取 `fetch_item_filter_options`，不要硬编码。

官方：
- https://docs.tikhub.io/443673045e0
- https://docs.tikhub.io/444247760e0

### C. Douyin App V3：补详情层

适合 L2/L3：

- 视频详情
- 用户详情
- 用户主页作品
- 评论
- 评论回复
- 分享链接解析

官方 TikHub Plugin 明确建议 Douyin 详细数据优先使用 App V3；Web 用户主页接口官方文档也提示“不稳定，尽量使用 APP 接口”。

官方：
- https://github.com/TikHub/tikhub-plugin/blob/main/skills/douyin/SKILL.md
- https://docs.tikhub.io/186826143e0
- https://docs.tikhub.io/186826223e0

## 3. 低粉账号发现不需要 Xingtu

普通用户搜索接口已经支持粉丝档：

- < 1K
- 1K–10K
- 10K–100K
- 100K–1M
- > 1M

因此 V1 没必要使用昂贵的 Xingtu Advanced KOL Search。

Xingtu 高级搜索官方标价 0.02 USD / 次；普通基础接口通常约 0.001 USD / 次。

官方：
- https://docs.tikhub.io/370212784e0
- https://docs.tikhub.io/383041376e0

## 4. 批量视频详情非常关键

Douyin App V3 的批量详情 V2：

- 一次最多 50 个视频
- 固定 0.001 USD / request
- APP 版本有明确删除/私密/部分可见状态说明

因此增长快照应该采用：

```text
50 IDs
↓
1 次 batch detail
↓
保存 metric_snapshot
```

而不是每条视频单独请求。

这会显著降低长期监控成本。

官方：
- https://docs.tikhub.io/339033805e0

## 5. TikHub 自带趋势接口，但不应替代我们的 metric_snapshot

`fetch_hot_item_trends_list` 可取：

- 点赞趋势
- 分享趋势
- 评论趋势
- 小时/天粒度

但它不包含播放趋势，而且一个指标就是一次请求。

因此：

- 日常大量监控：自己用 batch detail 定时做 snapshot
- 少量深研 Case：可调用 TikHub trend 接口补官方预计算趋势

官方：
- https://docs.tikhub.io/252393844e0

## 6. 评论采用三级策略

### L1
不拉评论。

### L2
优先尝试一调用：
`fetch_hot_comment_word_list`

如果真实测试证实普通候选也能返回有效词云，可作为低成本评论粗筛。

官方：
- https://docs.tikhub.io/252393843e0

### L3
再取原始热门评论。

APP V3 评论接口：

- cursor 翻页
- count 官方明确建议保持默认，否则可能出现 BUG

绝不默认全量翻页，因为每页都计费。

官方：
- https://docs.tikhub.io/186826225e0

## 7. 不下载高清原视频作为默认流程

最高画质接口官方标价 0.005 USD / 次。

V1 没有 NAS 需求，因此：

- L0/L1 不下载
- L2 只在 ASR / 视觉分析真正需要媒体时获取普通可用媒体
- 最高画质只用于极少数深研/制作资产

官方：
- https://docs.tikhub.io/312096107e0

## 8. TikHub 价格与预算

官方当前说明：

- 大多数基础请求：0.001 USD / request
- 非 200 通常不扣费
- 日请求数越高有阶梯折扣
- 有 `calculate_price`
- 有 `get_user_daily_usage`

系统应每天将 TikHub usage 拉回业务库，与我们自己记录的 external_api_call 对账。

官方：
- https://docs.tikhub.io/4579905m0
- https://docs.tikhub.io/186826051e0

## 9. TikHub Hosted MCP 的定位

TikHub 官方有 Hosted MCP 和 Claude Code Plugin，并带：

- trend-research
- competitor-analysis
- comments-analysis
- bulk-data-export

这些很适合：

- 开发阶段探索接口
- 人工临时研究
- 对照我们自己的结果

但不应作为自动采集主链路。

原因：

- Agent 可直接触发计费接口
- 不方便统一缓存
- 不方便统一预算
- 不方便统一落库
- 不方便复现“当时为什么调用”

正式链路仍使用 SDK + Windmill。

---

# 二、Windmill

## 1. Windmill 能真正替代什么

Community Edition 已可承担：

- Scheduler
- Worker
- Job queue
- Script runtime
- Flow orchestration
- retries
- error handler
- timeout
- result cache
- Secrets / Resources
- Full-code App hosting
- MCP Gateway
- job/run history
- script/flow/app version

因此 V1 不写：

- 独立 Scheduler
- 独立 Worker queue
- 独立 MCP Server
- 独立 Web API Server
- 独立 Secrets service

官方：
- https://www.windmill.dev/docs/advanced/self_host
- https://www.windmill.dev/docs/core_concepts/mcp

## 2. Full-code App，而不是 Low-code App

官方现在明确：

- Full-code：React / Svelte，新项目推荐
- Low-code：legacy，仍支持但不是未来重点

V1 研究台采用 React Full-code App。

好处：

- 页面体验可控
- Codex 容易维护
- frontend/backend 同目录
- 自动生成 `wmill.ts`
- 后端 runnable 可混用 Python / TypeScript / SQL
- Windmill 直接构建和部署

官方：
- https://www.windmill.dev/docs/full_code_apps
- https://www.windmill.dev/docs/getting_started/full_code_apps_quickstart

## 3. Codex 开发可以进一步省力

Windmill CLI：

`wmill init`

可生成：

- AGENTS.cli.md
- AGENTS.md
- .agents/skills/
- .claude/skills/
- resource type definitions

Full-code App 还能：

`wmill app generate-agents`

生成：

- AGENTS.md
- DATATABLES.md

这意味着项目初始化后，应先运行 Windmill 官方 AI context 生成器，再让 Codex 开发，不要人工重新写全部 Windmill 规范。

官方：
- https://www.windmill.dev/docs/misc/guides/local_dev_with_ai
- https://www.windmill.dev/docs/full_code_apps/cli_workflow

## 4. MCP 不用自研

Windmill MCP Gateway 可直接暴露：

- scripts
- flows
- selected endpoints

并支持：

- OAuth
- token
- folder scope
- self-hosted URL

推荐只暴露 `f/douyin_research` 文件夹下的 research tools，不让 Codex看到整个 workspace。

官方：
- https://www.windmill.dev/docs/core_concepts/mcp

## 5. Community Edition 有几个必须提前规避的限制

### Concurrency limits：不是免费功能

Windmill 自带全局 concurrency/rate limit 是 Cloud / Self-hosted Enterprise 功能。

所以 V1 不能依赖 Windmill 的该功能限制 TikHub API。

我们必须自己做：

```text
PostgreSQL api_rate_bucket
+
transaction / row lock
+
code token bucket
```

这样以后无论有几个 worker，都能共享全局限流状态。

官方：
- https://www.windmill.dev/docs/core_concepts/concurrency_limits

### Job debouncing：Enterprise

也不要把批处理成本优化建立在这个功能上。

我们自己按批次组成 50 个 ID 再调用 TikHub batch endpoint。

官方：
- https://www.windmill.dev/docs/core_concepts/job_debouncing

### Git Sync：多人 workspace 不能当基础设施依赖

Community Edition Git Sync 当前只对 <= 2 users 的 workspace 提供。

我们的研究台目标是“人人可看”，所以不要把 Windmill Git Sync 当成核心开发链。

采用：

```text
GitHub = source of truth
wmill CLI = pull/push
可选 GitHub Actions / 部署机执行 wmill sync push
```

官方：
- https://www.windmill.dev/docs/advanced/git_sync
- https://www.windmill.dev/docs/advanced/cli/sync

## 6. 用户访问限制

Community Edition 当前：

- 最大 50 users
- 最大 4 groups
- SSO 免费最多 10 users
- Guest：100 distinct emails / trailing 30 days

如果团队 <50：

- 普通成员可使用 Windmill 手工 email/password 账号
- 不受 SSO 10 人限制

如果只看研究台且希望更轻：

- 可考虑 Guest App
- 100 guest / 30 days 免费
- Guest 只访问指定 App，不进入 Windmill workspace

不要使用 Public anonymous 作为内部默认方案。

官方：
- https://www.windmill.dev/pricing
- https://www.windmill.dev/docs/core_concepts/authentication
- https://www.windmill.dev/docs/apps/guest_apps

## 7. 人工标注可正确记录真实操作人

App backend runnable 默认以 publisher 权限运行。

但 Windmill 会设置：

`WM_END_USER_EMAIL`

它代表实际 App Viewer。

因此写 human_annotation 时使用：

```python
actor = os.environ.get("WM_END_USER_EMAIL") or os.environ.get("WM_EMAIL")
```

不要直接用 WM_EMAIL，否则会把所有标注记录成 App publisher。

官方：
- https://www.windmill.dev/docs/apps/app-runnable-panel

## 8. App 安全

Full-code App 推荐：

- Members / Guests，不使用 anonymous public
- 开启 sandbox isolation
- 后端 runnable 参数严格白名单
- TikHub Token / DB password 放 Windmill Secrets/Resources
- MCP token 不入 Git

官方：
- https://www.windmill.dev/docs/full_code_apps/sandbox_isolation
- https://www.windmill.dev/docs/full_code_apps/deployment

## 9. Windmill 自带 cache 可以利用

Script / Flow / Flow step 可按照“相同输入 + TTL”缓存结果。

适合：

- TikHub filter options
- 内容标签
- 城市列表
- 近期榜单的极短 TTL
- 不频繁变化的账号基础信息

但业务历史仍必须入 PostgreSQL。

官方：
- https://www.windmill.dev/docs/core_concepts/caching

---

# 三、PostgreSQL

## 1. 两个数据库，单个 Postgres 实例即可起步

推荐 V1：

```text
postgres instance
├── windmill          # Windmill 自己的状态/队列
└── douyin_research   # 我们的业务数据
```

可以是同一 Postgres 容器/实例，但不要把业务表直接放进 Windmill 内部数据库 public schema。

原因：

- Windmill 升级/migration 与业务 Schema 解耦
- 备份/恢复边界清晰
- 将来迁移业务库更容易

## 2. JSONB 只保存原始响应，不替代结构化字段

核心字段仍结构化：

- video id
- account id
- metrics
- timestamps
- scores
- status

raw_payload 使用 JSONB 保存供应商原始响应。

若以后经常查 JSONB，可使用 GIN / jsonb_path_ops。

官方：
- https://www.postgresql.org/docs/current/datatype-json.html

## 3. 中文搜索不要高估 pg_trgm

PostgreSQL pg_trgm 对 LIKE / ILIKE / similarity 很有用，但中文支持与 locale/字符处理有关，短中文关键词也不一定能形成有效 trigram。

V1 搜索：

- ID / 作者：B-tree
- 标签/状态/日期：结构化索引
- 标题/description/transcript：ILIKE
- 常见中文短词：直接 substring 查询

数据规模上来后，优先评估 PGroonga。

PGroonga 官方定位就是 PostgreSQL 内多语言全文检索，并明确支持中文。

官方：
- https://www.postgresql.org/docs/current/pgtrgm.html
- https://pgroonga.github.io/
- https://pgroonga.github.io/reference/pgroonga-versus-textsearch-and-pg-trgm.html

## 4. V1 不做分区

metric_snapshot 未来可能很大，但现在先普通表 + (video_id, captured_at) 索引。

只有当实际数据量和查询计划证明需要时，再按 captured_at 做 range partition。

不要提前增加运维复杂度。

官方：
- https://www.postgresql.org/docs/current/ddl-partitioning.html

---

# 四、最终 V1 架构

```text
             Douyin
               │
          TikHub API
               │
      Official Python SDK
               │
        Windmill Scripts
               │
       Provider Normalize
               │
       PostgreSQL Business DB
        │          │
        │          ├── raw payload
        │          ├── metrics snapshot
        │          ├── score
        │          ├── analysis
        │          └── annotations
        │
   ┌────┴─────┐
   │          │
Full-code   Windmill MCP
React App       │
   │           Codex
团队查看/搜索    │
/历史/标注      深研/开发
```

Windmill 自己使用独立 windmill DB。

---

# 五、V1 最省钱的数据流

```text
L0 Billboard / Index
  ↓
基础数据入库
  ↓
纯代码去重
  ↓
纯代码黑马评分
  ↓
Top N
  ↓
App V3 batch detail（50/次）
  ↓
二次纯代码筛选
  ↓
极少量候选
  ├─ 评论词云（如可用）
  ├─ 评论 Top page
  ├─ ASR（按需）
  └─ 强模型精研
```

原则：

- 不全量评论
- 不全量 ASR
- 不全量下载视频
- 不逐视频详情请求
- 不把“统计问题”交给大模型
- 不让 Agent 自由翻页收费接口

---

# 六、开发前必须实测的未知项

官方文档仍不能替代真实响应，V0 必测：

1. Billboard 各榜单真实返回字段。
2. Index 与 Billboard 的视频 ID 是否可以无损对接 App V3。
3. `fetch_hot_comment_word_list` 是否对非榜单视频有效。
4. Billboard item trends 数据覆盖范围与历史长度。
5. batch App V3 响应中的播放/互动字段完整性。
6. 删除/私密/版权限制的真实状态映射。
7. 普通用户搜索的 `search_id` 翻页行为。
8. API 429 的真实阈值。
9. TikHub SDK 对 402/429/5xx 的 retry 行为。
10. `get_user_daily_usage` 与我们本地调用日志能否对账。

这些完成后才能冻结 Provider mapping。

---

# 七、当前不需要的组件

V1 暂时不需要：

- Appsmith
- Meilisearch
- Redis
- Kafka/RabbitMQ
- 独立 MCP Server
- 独立 API Server
- 独立 Scheduler
- 独立 Worker framework
- Supabase
- NAS
- Kubernetes
- TikHub Xingtu 高价接口
- TikHub HQ video API（默认）

---

# 八、当前技术风险排序

## 高

1. TikHub 第三方接口字段/稳定性变化
2. API 使用量失控
3. 中文搜索体验随历史量上升变差

## 中

4. Windmill CE 免费功能边界未来变化
5. 多 worker 下全局限流需要我们自己实现

## 低

6. PostgreSQL 数据规模
7. Web 研究台开发
8. MCP 接入

总体判断：V1 技术风险低，真正要控制的是第三方数据源与长期调用成本。
