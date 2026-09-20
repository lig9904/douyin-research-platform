# V1 部署与运行策略

日期：2026-09-19

## 1. 部署目标

V1 优先：
- 低成本
- 单机可运行
- 可备份
- 可迁移
- 不引入 Kubernetes / Redis / MQ
- 后续可横向扩展 worker

## 2. 当前验证基线

截至 2026-09-19：

- Windmill latest stable release：v1.815.0（2026-09-18）
- Windmill 官方当前 docker-compose：PostgreSQL 18
- TikHub Python SDK：2.1.1

原则：
- V0 可从当前验证版本起步
- 正式 V1 固定经过测试的具体版本
- 不在生产长期使用 `main` / floating `latest`
- 升级先跑 V0 provider + app smoke tests

## 3. 推荐拓扑

```text
Internet / Intranet
       |
 Reverse Proxy / TLS
       |
 Windmill Server
       |
 PostgreSQL instance
   |           |
 windmill   douyin_research
       |
 Windmill Worker(s)
```

V1 可全部部署在同一台 Linux 主机。

优先建议只在园区内网/VPN可达；如果直接暴露公网，必须：
- HTTPS
- reverse proxy rate limit
- 禁止 MCP URL token
- 强密码/最小权限
- 备份
- 定期升级安全版本

## 4. 容器

必需：
- PostgreSQL 18（compose 默认固定到已验证的 OCI digest）
- windmill_server
- windmill_worker
- windmill_worker_native

仓库 compose 的 Windmill 与 PostgreSQL 默认值均使用镜像 digest，而不是浮动 tag；
`WINDMILL_IMAGE` / `POSTGRES_IMAGE` 只用于受控升级或镜像代理覆盖。修改 digest 时必须
连同数据库备份恢复、App、MCP 和本机全链路回归一起审查，不能只改 tag。

可选：
- reverse proxy（已有 nginx / traefik 可直接复用；否则 Caddy）
- windmill_extra：只在需要 WebIDE LSP/debugger 时启用

生产第一版不需要：
- multiplayer
- indexer（EE全文 job/log search）
- windmill_extra（若主要通过 Git/Codex/CLI 开发）
- reports/chromium worker

不部署：
- Redis
- Kafka
- RabbitMQ
- Kubernetes
- Appsmith
- Meilisearch
- NAS

## 5. 初始 Worker

前期建议：
- default worker: 1 replica，约 1 vCPU / 1–2GB RAM
- native worker: 1 replica
- 后续根据排队情况增加 replicas

Windmill worker 一次执行一个普通任务，扩容方式是横向增加 worker。

官方 compose 默认 3 个普通 worker 是通用示例，不需要照搬到我们的小规模 V1。

## 6. PostgreSQL

单 PostgreSQL instance、两个 database：

- `windmill`
- `douyin_research`

不要把业务表混进 Windmill 内部 schema/database。

新部署直接使用 PostgreSQL 18；若未来跨 major version，按 Windmill 官方升级流程做整 cluster 备份/迁移。

业务库不使用 Windmill Data Tables 作为主存储，因为当前 Data Tables 对 workspace members 不实施数据库级 CRUD 权限隔离。

## 7. Secrets

TikHub API Key、LLM API Key、ASR Key、PostgreSQL 密码：

- 使用 Windmill Secret / Resource
- 不放 .env.example 的真实值
- 不提交 GitHub
- 不在脚本日志 print
- MCP token 使用 Authorization Bearer，不放 URL query

## 8. Git 与部署

GitHub 是源码事实源。

多人 workspace 不依赖 Windmill Community Git Sync（CE 内置 Git Sync 只支持 <=2-user workspace）。

推荐：

```text
Codex / Developer
      ↓
GitHub branch / PR
      ↓
main
      ↓
wmill sync push
      ↓
Windmill workspace
```

后续可增加 GitHub Actions 自动部署。

## 9. App

研究台采用 React Full-code App。

后端优先使用：
- .pg.sql：受控、参数化数据库查询
- Python：TikHub / 分析 / 复杂规则
- TypeScript：必要的应用逻辑

不额外建设 REST API Server。

不把 `douyin_research` 作为 workspace-wide Data Table 暴露给 Viewer。

## 10. MCP

Windmill 作为 MCP Server 给 Codex。

推荐：
- Streamable HTTP
- Authorization Bearer
- folder/tool scope 限定
- 只暴露 research tools
- 收费/写操作工具仍过 daily_budget

不要把 Token 写进 `?token=` URL。

## 11. 安全

- 研究台默认 Members，不匿名 Public
- Admin / Developer / Viewer 最小权限
- Viewer 不获得 Secrets/Resources read 权限
- 人工操作人读取 WM_END_USER_EMAIL
- TikHub 数据访问统一走后端 runnable
- 前端禁止持有第三方 API Key
- Docker 不挂载 host Docker socket
- 仅在确有需要时启用调试器/LSP
- reverse proxy 不记录 Authorization header

## 12. 备份

至少：
- 每日 pg_dump douyin_research
- 定期备份 windmill DB
- GitHub 保存 scripts/flows/apps/schema/prompts/rules
- 数据库/Windmill major upgrade 前强制备份
- 定期做 restore drill，而不只是“有备份文件”

本机在进入测试服务器前的权限、localhost HTTPS、Authorization 脱敏、限流和双数据库恢复演练，使用独立 stack，见 [LOCAL_SECURITY_ACCEPTANCE_V1.md](LOCAL_SECURITY_ACCEPTANCE_V1.md)。该演练不替代测试服务器上的真实账号、IdP、域名证书或异机备份验收。

## 13. 更新

生产固定：
- Windmill image digest
- TikHub SDK version
- PostgreSQL image digest 与 major version

升级流程：
1. 备份
2. 测试环境升级
3. V0 endpoint smoke tests
4. Provider normalization regression
5. Web App smoke test
6. MCP read/write tool smoke test
7. 再升级生产

## 14. 扩展条件

只有出现以下情况才增加组件：

- 搜索性能/中文体验明显不足 → PGroonga
- 单机 worker 长期排队 → 增加 worker / 分机
- 数据库成为瓶颈 → 独立 PostgreSQL 主机
- 媒体资产归档成为需求 → NAS / object storage
- 发布生产链成熟 → LibTV
