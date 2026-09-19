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

## 2. 推荐拓扑

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

## 3. 容器

必需：
- postgres
- windmill_server
- windmill_worker
- windmill_worker_native

可选：
- reverse proxy（如果已有 nginx / traefik，则不必使用官方 Caddy）
- LSP（仅 Web 编辑体验需要）

不部署：
- Redis
- Kafka
- RabbitMQ
- Kubernetes
- Appsmith
- Meilisearch
- NAS

## 4. 初始 Worker

前期建议：
- default worker: 1 replica，约 1 vCPU / 1–2GB RAM
- native worker: 1 replica，低资源
- 后续根据任务排队情况增加 replicas

Windmill worker 一次执行一个任务，扩容方式是横向增加 worker。

## 5. 数据库

单 PostgreSQL 实例、两个数据库：

- windmill
- douyin_research

不要把业务表混进 Windmill 内部 schema。

## 6. Secrets

TikHub API Key、LLM API Key、ASR Key、PostgreSQL 密码：
- 使用 Windmill Secret / Resource
- 不放 .env.example 的具体值
- 不提交 GitHub
- 不在脚本日志 print

## 7. Git 与部署

GitHub 是源码事实源。

多人 workspace 不依赖 Windmill Community Git Sync。

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

后续再增加 CI 自动 push。

## 8. App

研究台采用 React Full-code App。

后端优先使用：
- .pg.sql：数据库读写
- Python：TikHub / 分析 / 复杂规则
- TypeScript：必要的应用逻辑

不额外建设 REST API Server。

## 9. 安全

- 研究台默认 members/guests，不匿名 public
- Full-code App 开启 sandbox isolation（稳定性验证后）
- 人工操作人读取 WM_END_USER_EMAIL
- TikHub 数据访问统一走后端 runnable
- 前端禁止持有任何第三方 API Key

## 10. 备份

至少：
- 每日 pg_dump douyin_research
- 定期备份 windmill DB
- GitHub 保存所有 scripts/flows/apps/schema/prompts/rules
- 数据库升级前强制备份

## 11. 更新

生产不长期跟随不可控的 latest/main 镜像。

V0 验证可使用当前稳定版；
进入 V1 稳定运行后固定 Windmill image version，升级先在测试环境验证。

## 12. 扩展条件

只有出现以下情况才增加组件：

- 搜索性能/中文体验明显不足 → PGroonga
- 单机 worker 长期排队 → 增加 worker / 分机
- 数据库成为瓶颈 → 独立 PostgreSQL
- 媒体资产归档成为需求 → NAS / object storage
- 发布生产链成熟 → LibTV
