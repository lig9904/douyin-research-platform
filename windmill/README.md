# Windmill

V1 使用 Windmill 作为任务编排、脚本执行、内部 App 与 MCP Gateway。

## 计划目录

- `scripts/collectors/`：TikHub 等 Provider 采集
- `scripts/scoring/`：黑马、增长、账号基线等纯代码评分
- `scripts/analysis/`：L2/L3 研究任务
- `scripts/search/`：数据库查询与历史检索
- `scripts/reports/`：周报/统计
- `flows/`：L0→L1→L2→L3 工作流
- `apps/`：面向团队的研究台

## 原则

- Windmill Secrets 保存 Token，不提交 Git。
- 外部 API 调用前优先查数据库缓存。
- L0/L1 Flow 禁止调用 LLM。
- 高成本任务必须受每日预算与 Top-N 限制。
- 所有 Script/Flow 都要可单独重跑。
