# V1 RC2 发布说明

版本：`v0.1.0-rc.2`

Python package：`0.1.0rc2`

状态：GitHub Pre-release / 测试服务器候选基线

## 1. 这是什么版本

RC2 是在不可变的 [`v0.1.0-rc.1`](https://github.com/lig9904/douyin-research-platform/releases/tag/v0.1.0-rc.1)
本机验收基线上的增量候选版，用于专用外部反向代理与私网应用服务器的测试环境。
它不是测试服务器已验收证明，也不是生产发布。部署必须记录 RC2 tag、完整 commit、
镜像 digest、外部反代 FQDN 和目标环境证据。

RC1 tag 不会被移动、改写或复用；需要此部署拓扑时必须从 RC2 或后续明确的 release tag 检出。

## 2. 相对 RC1 的核心变化

### 外部反代测试服务器 overlay

- 输入：测试服务器的 RFC1918 绑定地址、目标 HTTPS FQDN、外部反代机的来源地址，
  以及由受控 Secret surface 提供的运行配置；来源地址用于宿主防火墙 ACL，不写入 Compose。
- 输出：Windmill 仅绑定指定私网地址的 `:8000`；PostgreSQL 仅在 Compose 网络内可访问；
  应用服务器不部署 Nginx。
- 接口：`docker-compose.test-server-external-proxy.yml`、
  `.env.test-server-external-proxy.example` 与发布脚本的外部反代 overlay 选项。
- 协作：专用反代机负责 TLS 与公网入口；应用服务器负责 Windmill 和 PostgreSQL；双方以
  FQDN、来源 ACL 与健康检查约定对接。
- 验收：Compose 渲染仅出现一条私网 `:8000` 映射，PostgreSQL 没有宿主机端口；外部反代
  通过受限来源访问健康检查。现场未验收前不得开放公网或声明生效。

### 失败关闭的运行时 smoke

- 输入：Docker/Compose 可用性、选定 overlay、渲染后的 Compose topology 与绑定地址。
- 输出：部署前明确的通过/失败结果及结构化证据。
- 接口：外部反代配置校验器、发布脚本与隔离运行时 smoke。
- 验收：Docker 或 Compose 缺失、地址不是 RFC1918、错误包含 Nginx/公开绑定、或 PostgreSQL
  映射到宿主机时，检查必须在启动服务前失败。

## 3. 仍需测试服务器现场验收

1. 外部反代 FQDN、正式 TLS 与来源 ACL；
2. 真实 admin/reviewer/viewer ACL 和审计留存；
3. 服务器 Secret、受限 TikHub/Ark/ASR 小流量 smoke 与费用对账；
4. 异机备份恢复、告警闭环和回滚演练。

这些验收通过也不自动授权生产。生产 FQDN、身份、Secret、预算、RPO/RTO、告警值班和
变更窗口仍需独立确认。

## 4. 部署与回滚

部署前按 [`TEST_SERVER_READINESS_V1.md`](../TEST_SERVER_READINESS_V1.md) 记录基线并先备份。
仅从 RC2 或后续明确 release tag 检出，不从浮动分支部署。验证失败时停用 schedule/worker
和外部 runnable，保留原库只读，并按已校验的备份执行恢复演练，不覆盖未知状态的数据源。
