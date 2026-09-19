# V1 部署基线

日期：2026-09-19

## 1. 不追 main 漂移

Windmill 官方自托管示例 .env 默认使用 ghcr.io/windmill-labs/windmill:main，这适合快速体验，不适合我们的长期生产部署。

当前正式 Release 已到 v1.814.0（2026-09-17）。

V1 原则：
- 开发验证阶段可跟随官方推荐方式快速起环境
- 进入团队正式使用前固定一个已验证的 Windmill Release / image digest
- 升级通过单独变更和回归测试完成
- 不允许生产环境因为 main 更新而自动漂移

## 2. PostgreSQL 版本必须一起固定

Windmill v1.814.0 release 对应的官方 docker-compose 使用 PostgreSQL 18，并将 volume mount 到 /var/lib/postgresql。

PostgreSQL 18 与 16 的官方镜像数据目录布局不同。

因此：
- 新环境直接选定 PostgreSQL major
- compose、volume mount、备份恢复文档作为同一个版本单元管理
- 不单独修改 postgres image tag
- major upgrade 前先整集群备份

## 3. 一个 PostgreSQL 实例，两个逻辑数据库

V1 推荐：

postgres cluster
- windmill
- douyin_research

优点：
- 省资源
- 备份简单
- Windmill 与业务 Schema 逻辑隔离

注意：备份策略要覆盖整个 cluster，而不是只备份 windmill。

## 4. 最小容器

第一阶段：
- PostgreSQL
- windmill_server
- windmill_worker
- windmill_worker_native（按官方 compose/实际需要）
- Caddy 或现有反向代理

不启用：
- Enterprise indexer
- reports/chromium worker（没有需求时）
- 额外 Kafka/Redis
- Kubernetes

## 5. 资源规划

初期任务以 HTTP API、SQL 和轻量 Python 为主，不是重计算系统。

建议先小规格运行，依据：
- job queue backlog
- worker CPU / memory
- PostgreSQL connections
- API latency

再扩 worker。

不要预先堆服务器。

## 6. 安全

- TikHub API Key：Windmill Secret
- DB credentials：Resource/Secret
- GitHub 不存任何 Secret
- App publisher 使用受限账号
- 普通团队成员 Operator
- worker sandbox isolation 开启
- 不挂 host Docker socket，除非以后确有可信 Docker job 需求
- 外网部署必须 HTTPS

## 7. 健康监测

至少监控：
- Windmill /api/health/status
- PostgreSQL health
- queue backlog
- worker availability
- TikHub API success/error rate
- daily cost reconciliation

## 8. 备份

在没有 NAS 前也必须备份数据库。

V1 最低要求：
- 每日自动数据库备份
- 保留多版本
- 定期做恢复测试

业务真正不可替代的是：
- 历史 discovery
- metric snapshots
- 人工标注
- 分析结果

媒体文件暂时不是第一阶段关键资产。

## 9. 升级策略

升级前：
1. 读取 Windmill release notes
2. 备份 PostgreSQL cluster
3. 在开发/测试环境验证
4. 检查 Full-code App
5. 检查 MCP
6. 检查 schedule/trigger
7. 再升级生产

Windmill 发布频率很高，所以不需要追每一个版本；以稳定和功能需求驱动升级。
