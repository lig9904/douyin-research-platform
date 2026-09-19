# 现成组件采用/排除矩阵

日期：2026-09-19

| 组件 | 决策 | 用途 | 不采用/限制 |
|---|---|---|---|
| TikHub Douyin Billboard | 采用 | 热点、黑马、榜单发现 | 第三方数据源，必须抽象 Provider |
| TikHub Douyin Index | 采用 | 垂类/关键词/精选标签搜索 | 筛选项动态读取，不硬编码 |
| TikHub Douyin App V3 | 采用 | 视频/账号/评论/批量详情 | 只对入选样本补数据 |
| TikHub Python SDK | 采用 | Windmill 采集脚本 | 固定验证版本，升级需回归 |
| TikHub Hosted MCP | 辅助 | 人工探索、临时研究 | 不作为自动流水线，防止 Agent 无预算调用 |
| TikHub Claude Plugin | 参考 | 研究官方推荐工作流 | 不作为生产依赖 |
| TikHub n8n Integration | 排除 V1 | — | 当前 README 主要覆盖 TikTok/Instagram/YouTube/Twitter，未覆盖本项目核心 Douyin Billboard/Index/App V3 |
| Windmill Community | 采用 | 调度、Worker、Flow、Secrets、App、MCP | 全局 concurrency/debounce 等高级能力需自补 |
| Windmill Full-code App | 采用 | React 研究台 | Low-code legacy 不作为长期主界面 |
| Windmill MCP Gateway | 采用 | Codex 工具入口 | 仅暴露受控 folder/tools |
| Windmill Git Sync | 不作为依赖 | — | CE 多人 workspace 有限制；GitHub + wmill CLI |
| PostgreSQL | 采用 | 唯一业务事实库 | Windmill DB 与业务 DB 分离 |
| PostgreSQL ILIKE | 采用 V1 | 初期中文关键词检索 | 数据大后体验有限 |
| pg_trgm | 仅辅助 | 模糊检索 | 不作为最终中文全文检索 |
| PGroonga | 后置评估 | 中文全文检索 | V1 不提前部署 |
| Appsmith | 排除 V1 | — | Windmill Full-code App 已覆盖 |
| Meilisearch | 排除 V1 | — | 前期不需要独立搜索服务 |
| Redis | 排除 V1 | — | Windmill/Postgres 足够 |
| n8n | 排除 V1 | — | 项目以代码/SQL/规则为主，Windmill更匹配 |
| NAS | 后置 | 媒体资产 | V1 不存大量媒体 |
| LibTV | 后置 | 生产链 | 研究闭环先跑通 |
| Kubernetes | 排除 V1 | — | 单机 Docker Compose 足够 |

## 选择标准

1. 是否直接减少我们必须维护的代码。
2. 是否完整覆盖 Douyin 研究主链。
3. 是否利于“代码优先、LLM 最后”。
4. 是否支持成本闸门、历史回看和可追溯。
5. 是否会增加不必要的服务和运维。
