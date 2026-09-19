# 本机 Windmill 可重复导入 V1

本说明只作用于隔离的 `l3-review-local` Docker/Colima 环境。同步不会调用 TikHub、ASR 或 LLM；它只将 Git 中的 scripts、flows、Full-code App、Resource 模板和非敏感预算默认值写入本机 Windmill workspace。数据库 Secret 和审核人 allowlist 只存在于本机运行配置，不进入 Git。

## 受控源与目标

- 源：`windmill/f/content_research/**`
- manifest：`windmill/wmill.yaml`
- 目标：`content-research-local`，`http://127.0.0.1:18000`
- 可选的 CLI 配置位置：`~/.config/windmill-l3-review-local`，或显式 `WMILL_CONFIG_DIR`

`wmill.yaml` 固定排除 Secret、schedule、trigger、users、groups、workspace settings、workspace key 与 Folder ACL。它保留 collectors 的代码/Flow 定义，但不会运行它们；收费 collection 仍要求其自身的 `execute=true`、确认字串、预算与 Secret。

Folder 元数据明确不从 Git 同步（`skipFolders: true`）。原因是 reviewer/admin/Viewer ACL
属于目标环境身份配置，不能把本机用户路径或 allowlist 带到测试服务器。测试服务器部署时
必须单独创建 `f/content_research` 的最小 Folder ACL，并用至少 reviewer/admin 与普通 Viewer
两个真实账号验收；源码同步成功不等于 ACL 已生效。

## 当前已部署状态与认证边界

目标 workspace 已完成同步，Full-code App、Flow、Resource 和变量都已在本机 Windmill 持久化。部署使用仓库外、权限为 `0600` 的本机 CLI profile，未把 token 写入仓库、文档或脚本。`scripts/local-windmill-sync.sh` 仍不接收 token 参数；重跑 `metadata` / `sync` 前必须在仓库外准备该 profile。认证缺失时命令在写入前失败。

当前 UI 的“导入”菜单已实测只暴露按对象导入（Hub project、Flow、Full-code App、Workflow-as-Code、low-code App）。其中未出现 Resource、Variable 或整个 `windmill/` 源树的导入入口；因此后续重跑仍使用受控 CLI sync，不用逐项 UI 粘贴替代源目录。

最小完整同步的单点是 Windmill CLI 的已认证 workspace profile。没有该 profile，`wmill generate-metadata` 和 `wmill sync push` 必须失败关闭，不应从数据库反向读取 token，也不应将持久 token 写入 Git。

## 导入与重跑

```bash
scripts/local-windmill-sync.sh preflight
scripts/local-windmill-sync.sh metadata
scripts/local-windmill-sync.sh sync
scripts/local-windmill-sync.sh status
```

`metadata` 只在本地生成/刷新 Windmill lock 与 schema 元数据。`sync` 不传 `--yes`，并拒绝非交互终端；必须人工审阅 CLI 的 scoped diff 后确认，避免把 EOF 误报成已部署。日常同步固定带 `--skip-secrets --keep-deleted`：可创建或更新 Git 管理的对象，但绝不删除本机远端对象。这是保留未入 Git 的 Secret 和本地 reviewer allowlist 的硬边界，禁止从日常同步命令中移除。如需删除对象，必须用单独、显式、可复核的管理流程。

L3 三个 Python backend 的 requirements 使用 Windmill 可解析的无空格 PEP 508 Git 写法，并通过 `generate-metadata` 生成 lock。依赖固定到提交 `1558677c40cc22239e660e269738619dfd05388d`；不得手写或复用空 lock，也不能只凭 bundle 成功就认定 Python 运行依赖可用。

当 CLI profile 已就绪时，`metadata` / `sync` 会更新本机 workspace；profile 缺失时必须在写入前退出。首次部署已经完成，这一条说明的是后续可重复运行边界，不是未部署状态。

## 配置与失败关闭

同步会创建：

- `f/content_research/research_db` PostgreSQL Resource 模板；
- `f/content_research/l3_budget_preview_config`，无 Provider、无成本、无预算的 placeholder。

当前本机已配置 `research_db_password` Secret 和 `l3_privacy_reviewers` 非 Secret allowlist，Resource 已引用该 Secret。文档、Git 和验收输出都不包含其值。日常 sync 不负责创建它们，但必须通过 `--keep-deleted` 保留。

审核 allowlist 已完成本机配置。预算默认值仍保持无 Provider、无成本的失败关闭状态；小额真实 Provider 验证使用单独的已审核配置，不在此源管理默认值中填充私密数据。

## 持久性验收

导入后执行：

```bash
scripts/local-windmill-sync.sh status
scripts/local-l3-env.sh stop
scripts/local-l3-env.sh start
scripts/local-windmill-sync.sh status
```

同一部署版本停启前后的计数必须一致。当前活动对象基线为 `app=1`、`raw_app=0`、`script_active=3`、`flow=3`、`resource=1`、`variable_nonsecret=2`、`variable_secret=1`、`schedule=0`、`trigger=0`。`raw_app=0` 不代表未部署：Windmill CE v1.815.0 将该 Full-code App 记入 `app` 表。`script_archived` 是 App backend runnable 更新留下的历史版本，每次部署后可增长，不用作固定数量验收项。停机命令不删除 volume；不得用 `down -v` 验收持久性。
