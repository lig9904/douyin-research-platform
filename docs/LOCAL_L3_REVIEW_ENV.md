# 本机隔离 L3 正文审核环境

日期：2026-09-20

## 范围

这是一个仅用于本机验证的隔离 Windmill + PostgreSQL 环境。它不部署到生产，不读取 TikHub、ASR 或 LLM Provider Key，不启用付费执行器，也不连接已有 Docker Compose 项目。

固定隔离边界：

- Colima profile：`l3-review-local`
- Docker context：`colima-l3-review-local`
- Compose project：`l3-review-local`
- Windmill：`127.0.0.1:18000`
- PostgreSQL：`127.0.0.1:15432`
- 虚拟机公共 DNS：`114.114.114.114`、`223.5.5.5`（仅该专用 profile）

启动脚本会修复该 profile 某些 Ubuntu 镜像中缺失的 `systemd-resolved` stub，仅安装仓库内的 `config/l3-local-resolv.conf`；不会修改 macOS DNS 或其他 Colima profile。
专用 Docker daemon 同时限制为单层并发下载，以降低不稳定代理链路拉取大型 Windmill 镜像时的失败率；配置只作用于本 profile。

不要用裸 `docker compose up`、当前 Docker context，或仓库默认 `.env` 启动本环境。

## 初始化与启动

先生成仅本机可读的环境文件；命令生成三组随机 hex 密码且不输出其值：

```bash
scripts/local-l3-env.sh init
scripts/local-l3-env.sh start
```

`start` 会创建/启动专用 Colima profile，拉取镜像、启动四个容器、重复执行 reviewer role provision，并完成 loopback、Windmill 健康检查及数据库权限检查。

查看、再次 provision、再次验证和停止：

```bash
scripts/local-l3-env.sh status
scripts/local-l3-env.sh provision
scripts/local-l3-env.sh verify
scripts/local-l3-env.sh stop
```

`stop` 只移除该项目的容器和 network，保留 named volumes。此脚本没有 `down -v` 命令；若要删除本地证据库，必须单独审查明确的删除操作。

## 数据库角色

`02-bootstrap-l3-local-reviewer.sh` 每次 provision 都确保存在登录角色 `l3_local_reviewer`。该角色：

- 默认 `default_transaction_read_only=on`；
- 只能连接 `douyin_research`、使用 `public` schema；
- 只能读取正文审核与本机 MCP 必需的 `source_video`、`research_promotion_decision`、`video_comment_feature_snapshot`、`transcript`、`metric_snapshot`、`analysis_run`、`research_task_cost` 与 `daily_budget`；
- 对 `human_annotation` 无读写授权，对 `daily_budget` 无写授权，对其他业务表也无授权。
- 对数据库 `CREATE`/`TEMPORARY` 和 `public` schema `CREATE` 无权限。

Docker 初始化钩子只会在新 PostgreSQL volume 时自动运行。已有本环境 volume 必须执行 `scripts/local-l3-env.sh provision`，而不是删除 volume；基础 schema 的新环境由 `db/schema.sql` 创建，已有数据库的 schema 演进仍须按 `db/migrations/*.sql` 显式执行。

## 正文审核页

此 Compose 环境不把正文审核页发布为 Windmill App，也不把正文写入 Windmill Job 输出。正文审核页必须由宿主机的 reviewer-only CLI 单独运行，并只监听回环地址；它使用 `l3_local_reviewer` 读取受限表，不能写审批、预算或执行记录。

Windmill 中的候选/批准/预算预览流程继续只展示 manifest 和指纹。只有审核人在宿主机正文页完成核对后，才能回到 Windmill 进行对同一指纹的批准。

从 Windmill 待审摘要区取得“审核对象 ID”、审核版本和指纹后，只通过受控脚本启动正文页：

```bash
scripts/local-l3-env.sh review <video-id> <privacy-review-version> <fingerprint>
```

脚本只会构造 `l3_local_reviewer@127.0.0.1:15432/douyin_research` 的 DSN；CLI 同时拒绝 owner、外部主机、不同端口或非本机数据库。一次性浏览器能力只放在 URL fragment 中并由系统浏览器直接打开，不输出到终端。正文 session 只允许成功读取一次，刷新或后退不会再次返回正文；页面还会在超时、离页或浏览器后退缓存恢复时清空正文 DOM。

本页无法防御同一 macOS 账号下的恶意进程、可读浏览器数据的扩展、屏幕录制或系统 root；这些属于本机操作系统账户边界。若未来改为非回环监听或反向代理，必须重新设计 HTTPS、`Secure` cookie 与真实身份认证，不能直接复用本实现。

本机验收已用合成 L2 候选跑通上述页面：manifest 指纹与正文页重建结果一致，正文 session 成功读取一次，Windmill 保存一条绑定该指纹的 `l3_privacy_review`，随后预算预览返回 `budget_missing`。该链路的 prepare/approve/budget 分别保持外部调用 0、LLM 调用 0；预算预览固定 `paid_execution_available=false`。

## 本机验证与真实 ACL 的区别

本机单管理员账号只能验证：端口隔离、数据库最小权限、任务日志不应接收正文，以及 `WM_END_USER_EMAIL` 缺失时失败关闭。它不能证明多用户 Folder ACL 或身份提供方行为；真实 reviewer/admin/Viewer ACL 仍必须在独立多账号 Windmill 环境现场验收。

`.env.l3-local` 为 mode `0600` 且已被 `.gitignore` 的 `.env.*` 规则忽略。不要把它、容器日志、数据库 dump 或正文证据上传到 Git。
