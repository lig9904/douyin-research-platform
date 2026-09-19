# Multi-platform Content Research Platform

多平台热点采集、对标研究、黑马发现与 AI 精研平台。当前 V1 以抖音作为首个落地平台，但数据库、Provider Contract、L0/L1 和后续 Web 均按多平台架构设计。

> 仓库名与 Python package 中仍保留 `douyin` 历史命名，用于兼容现有代码；它们不再代表产品只能研究抖音。

## V1 目标

V1 先解决四件事：

1. 先用现成接口采集抖音热点、黑马视频、账号、作品和必要评论，同时保留未来接入快手、视频号、小红书、B站、微博等平台的统一入口。
2. 采用“先粗后细”的分层漏斗，尽可能用代码、SQL、统计和规则完成筛选。
3. 提供人人可访问、可看懂、可搜索、可回看历史的多平台 Web 研究台。
4. 通过 MCP 向 Codex 暴露经过缓存和治理后的研究数据与任务，而不是让 Codex 无限制直接调用外部收费接口。

## 当前技术路线

```text
Platform Registry
        ↓
PlatformResearchProvider
        ↓
┌──────────────────────────┐
│ Douyin → TikHub（已落地） │
│ Kuaishou → planned       │
│ WeChat Channels → planned│
│ Xiaohongshu → planned    │
│ Bilibili → planned       │
│ Weibo → planned          │
└──────────────────────────┘
        ↓
     Windmill
 ┌──────┼────────┐
 │      │        │
脚本    Flow     App
 │      │        │
 └──────┴────────┘
        ↓
   PostgreSQL
        ↑
 Windmill MCP
        ↑
      Codex
```

## 平台策略

当前平台注册：

- 抖音：已启用，TikHub 为首个 Provider
- 快手：预留
- 视频号：预留
- 小红书：预留
- B站：预留
- 微博：预留

Canonical ID 始终使用：

- 账号：`platform + platform_account_id`
- 视频：`platform + platform_video_id`

不同平台的数据可以统一管理，但**黑马评分、增长比较和粉丝效率默认只在同一平台内部计算**，不把不同平台原始指标直接混算。

## 核心原则

- 现成接口优先，不自研高维护成本爬虫。
- 平台显式：业务数据不允许隐式默认“douyin”。
- 代码优先：SQL / 规则 / 统计能解决的问题禁止调用大模型。
- 先粗后细：全量低成本采集，逐级缩小进入精研的样本。
- 成本可控：缓存、调用预算、分层限额、成本记录。
- 数据可追溯：保存平台、发现来源、原始响应、指标快照、规则版本、AI 分析版本。
- Provider 可替换：业务逻辑不得绑定 TikHub 或其他数据商私有字段。
- 历史可回看、可搜索、可人工标注。
- V1 不以 NAS、LibTV 自动化和成片归档为前置条件。

## 目录

- `src/douyin_research/providers/`：通用 Provider Contract 与具体平台适配器。
- `src/douyin_research/l0l1/`：多平台可复用的 L0/L1 入库、预算和评分核心。
- `windmill/`：采集、评分、分析脚本，Flows 与研究台 App。
- `db/`：数据库 Schema 和迁移。
- `rules/`：黑马、过滤、成本闸门等确定性规则。
- `prompts/`：仅用于必须调用大模型的精研任务。
- `docs/`：架构、接口核验和开发任务。
- `tests/`：规则与集成测试。

## 状态

当前阶段：

- Windmill + PostgreSQL 基础环境：完成
- TikHub Provider + 缓存层：完成
- L0/L1 纯代码粗筛：完成
- 多平台基础抽象：完成
- TikHub 真实付费接口 V0：已用本机登录账号的现有 Secret 完成 1 次低粉爆款榜 smoke；SDK 2.1.1、零重试、业务成功并返回 4 项，Secret 未写入仓库或日志
- Web 研究台：已在本机 Windmill CE 部署，已验收首页、视频库、账号库、热点库、全局搜索、运行/成本只读页，以及 L3 待审摘要→一次性正文页→审核保存→零调用预算预览全链路
- 本机只读 MCP：stdio 三项工具已使用受限 reviewer 角色完成真实业务库集成冒烟；写入尝试由 PostgreSQL 拒绝
