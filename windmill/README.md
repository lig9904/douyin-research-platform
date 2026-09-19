# Windmill

V1 使用 Windmill 作为任务编排、脚本执行、内部 App 与 MCP Gateway。

## 目录

- `f/content_research/research_dashboard.raw_app/`：研究台 Full-code App。
- `f/content_research/collectors/`：受缓存、预算和硬上限保护的采集脚本。
- `f/content_research/flows/`：面向业务的编排入口。

首个采集入口是 `manual_comment_collection`。它只允许人工运行，默认预览，正式执行需要精确确认、当天预算和数据库前置检查。详见 `docs/WINDMILL_COMMENT_COLLECTION_V1.md`。

## 原则

- Windmill Secrets 保存 Token，不提交 Git。
- 外部 API 调用前优先查数据库缓存。
- L0/L1 Flow 禁止调用 LLM。
- 高成本任务必须受每日预算与 Top-N 限制。
- 所有 Script/Flow 都要可单独重跑。
- 付费入口默认零调用，不附带 schedule，不自动创建或扩大预算。
