# Architecture Decisions

## ADR-001：不自研抖音爬虫

V1 使用成熟第三方 API/SDK。原因：降低反爬、签名、风控、Cookie、接口变化等长期维护成本。

## ADR-002：Windmill 作为 V1 编排与研究台底座

第一阶段不自研 Scheduler、Worker、任务队列、Secrets 管理、MCP Server 和独立 Web 后台。

## ADR-003：PostgreSQL 为唯一事实数据库

所有研究状态、快照、分析和人工标注以 PostgreSQL 为准。NAS 后置。

## ADR-004：代码优先，LLM 最后

任何确定性任务若能由 SQL、普通代码、规则、统计或轻量算法完成，不允许使用 LLM。

## ADR-005：先粗后细

L0/L1 不使用 LLM；只有逐级晋级的少量样本进入高成本分析。

## ADR-006：数据源抽象

TikHub 是 V1 Provider，不是业务模型。数据库核心实体禁止直接复制 TikHub 私有字段命名。

## ADR-007：所有分析可重算

采集与分析解耦。更换规则/模型/Prompt 时，应复用已有原始数据重新分析，而不是重新采集。
