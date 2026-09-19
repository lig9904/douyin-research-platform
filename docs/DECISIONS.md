# Architecture Decisions

## ADR-001：不自研抖音爬虫

V1 使用成熟第三方 API/SDK。原因：降低反爬、签名、风控、Cookie、接口变化等长期维护成本。

## ADR-002：Windmill 作为 V1 编排与研究台底座

第一阶段不自研 Scheduler、Worker、任务队列、Secrets 管理、MCP Server 和独立 Web 后台。

研究台使用 Windmill Full-code App（React），不基于已标记为 legacy 的 Low-code App 新建长期界面。

## ADR-003：PostgreSQL 为唯一事实数据库

所有研究状态、快照、分析和人工标注以 PostgreSQL 为准。NAS 后置。

Windmill 状态数据库与 douyin_research 业务数据库逻辑分离；V1 可部署在同一 PostgreSQL 实例中。

## ADR-004：代码优先，LLM 最后

任何确定性任务若能由 SQL、普通代码、规则、统计或轻量算法完成，不允许使用 LLM。

## ADR-005：先粗后细

L0/L1 不使用 LLM；只有逐级晋级的少量样本进入高成本分析。

## ADR-006：数据源抽象

TikHub 是 V1 Provider，不是业务模型。数据库核心实体禁止直接复制 TikHub 私有字段命名。

TikHub 接口按三层使用：Billboard/Index 负责发现，App V3 负责入选样本补详情。

## ADR-007：所有分析可重算

采集与分析解耦。更换规则/模型/Prompt 时，应复用已有原始数据重新分析，而不是重新采集。

## ADR-008：不依赖 Windmill Enterprise 限流能力

Community Edition 的 Concurrency Limits / Job Debouncing 不作为架构依赖。

V1 使用 PostgreSQL + 代码实现全局 API rate limit、预算闸门和批量聚合。

## ADR-009：GitHub 是源码事实源

由于 Community Edition 内置 Git Sync 对多人 workspace 有限制，不依赖 Windmill Git Sync。

使用 GitHub + Windmill CLI（wmill sync push/pull）作为基础开发与部署路径。

## ADR-010：中文搜索分阶段

V1 使用结构化索引 + ILIKE/substring 满足早期搜索。

数据规模和体验达到阈值后优先评估 PGroonga；不把 PostgreSQL 原生英文向 FTS 或 pg_trgm 当作最终中文搜索方案。

## ADR-011：Windmill App 内操作人使用 WM_END_USER_EMAIL

App 后端默认以 publisher 权限执行。

所有人工标注、收藏、人工判定必须使用 WM_END_USER_EMAIL 获取实际 viewer，避免把操作统一记到 publisher。

## ADR-012：V1 普通成员使用 Windmill Operator 账号

Guest App 需要 OAuth/SSO 或自建 JWT issuer，会增加不必要的身份系统工作量。

V1 在团队规模适合 Community Edition 的前提下，直接手工创建普通 Windmill 用户并赋予 Operator 角色。开发人员使用 Developer 角色。

## ADR-013：TikHub 主域名使用 api.tikhub.io

TikHub 最新官方 API 文档明确要求优先使用 https://api.tikhub.io，并提示避免优先使用 api.tikhub.dev，因为可能出现更慢响应和性能下降。

若部署环境网络质量不佳，单独通过网络/proxy解决，不默认切换到 dev 域名。

## ADR-014：研究 App 使用权限受限的 publisher

App runnable 默认以 publisher 权限执行。

研究台不得由 superadmin 身份作为长期 publisher。应使用权限受限的普通用户，仅授权 douyin_research 数据库资源、本项目文件夹及必要 secrets。

## ADR-015：数据库查询优先使用 Windmill 原生 PostgreSQL runnable

列表、详情、搜索、统计等确定性数据库访问优先使用 .pg.sql 参数化查询。

只有需要复杂业务逻辑时才使用 Python/TypeScript，避免增加 ORM、独立 API 层和 SQL 注入风险。
