# 本机只读 MCP V1

## 范围

`python -m douyin_research.mcp.server` 是一个仅供本机验证的 stdio MCP
入口。它不是 Windmill MCP Gateway 的替代品，也不会连接 Windmill HTTP API。

固定只提供五个无副作用工具：

- `search_cases`：受限 canonical 视频案例元数据与指标；
- `get_case_detail`：一条案例及已完成 L3 结构化研究的白名单字段/成本；
- `get_cost_summary`：任务成本和日预算状态。
- `get_daily_briefing`：公开标题、合并指标、字段来源、规则优先级和 L3 完成状态；
- `get_research_briefs`：研究任务范围、频率、下次执行与最近一次安全运行状态。

它允许返回公开视频标题和账号昵称，便于真正按内容检索；不会返回描述、转写、评论正文、L3 自由文本、URL、Provider 原始载荷、任务 key、证据指纹、人工标注、数据库错误或凭据。完整原始记录由研究台按视频 ID 下钻查看，不通过 MCP 一次性外发。L3 只返回各白名单输出字段的数量、推断/隐私布尔标记和成本元数据。没有 refresh、采集、模型执行、预算预占、Secret 或 Windmill 管理工具。

## 本机启动

使用单独的最小权限、只读 PostgreSQL role 配置环境变量；不要使用 owner 或生产 DSN：

```bash
export DOUYIN_RESEARCH_MCP_DATABASE_URL='postgresql://readonly_role:...@127.0.0.1:15432/douyin_research?sslmode=disable'
uv run python -m douyin_research.mcp.server
```

MCP 使用 JSON-RPC stdio：stdout **只能**承载协议消息，stderr 仅有固定的本地配置失败信息。不要在 shell 历史、配置文件、命令行参数或聊天中放入 DSN/密码。建议使用本机受限的环境注入方式启动。

推荐的数据库授权最小集：

- `CONNECT` 至研究数据库、`USAGE` 至所需 schema；
- `SELECT` 至 `source_video`、`source_account`、`merged_video_metric`、`metric_snapshot`、`video_score`、`discovery_event`、`research_promotion_decision`、`analysis_run`、`research_task_cost`、`external_api_call`、`supplier_daily_spend`、`daily_budget`、`research_brief` 与 `research_brief_run`；
- 禁止 `INSERT`、`UPDATE`、`DELETE`、`CREATE`、`TEMPORARY` 和任何 Windmill 状态库权限。

即使角色配置错误，每个 MCP 工具也以 `BEGIN READ ONLY` 事务运行，并在本事务设置 1 秒锁等待、3 秒查询上限；查询使用固定 SQL 和参数绑定，没有客户提交的 SQL、排序字段、表名或 Provider 参数。

## 最小验证

协议和核心工具调用可通过：

```bash
uv run pytest tests/test_readonly_mcp.py -q
```

这验证 `initialize`、`tools/list`、`tools/call(search_cases)`、输入拒绝、固定脱敏错误和无写/Provider surface。它是合成 query service 验证，不是数据库集成或 Windmill 部署证明。

隔离本机数据库启动后，可运行真实集成冒烟；命令只从 mode `0600` 的本机
`.env.l3-local` 注入受限 reviewer 凭据，pytest 输出不会包含 DSN、密码或正文：

```bash
scripts/local-l3-env.sh start
set -a; source .env.l3-local; set +a
RUN_LOCAL_MCP_INTEGRATION=1 uv run pytest tests/test_readonly_mcp_local_integration.py -q
```

该测试验证 MCP 的 `initialize`、`tools/list` 与五项只读工具，并直接确认
reviewer 对 `source_video` 没有 `INSERT` 权限且实际写入被拒绝。没有显式设置
`RUN_LOCAL_MCP_INTEGRATION=1` 时会 skip，避免普通单测意外连接本机数据库。

原三项工具曾在隔离数据库完成冒烟并证明写入以 PostgreSQL `25006` 失败；新增两项仍需随本轮代码在测试服务器 reader role 上重新完成五工具冒烟。旧证据不能自动覆盖新增查询表权限。

## 与正式 Windmill MCP 的差距

正式接入仍应使用 `docs/CODEX_WINDMILL_MCP_V1.md` 规定的 Windmill
Gateway：HTTPS、Bearer 环境变量、关闭 URL token、限定
`mcp:scripts:f/content_research/research_tools/*`
folder、最小 token/Folder ACL 与反代 Authorization-header 脱敏。本地 MCP 不创建
Windmill token、账号、workspace、resource、变量或 App，也不证明多用户 ACL、TLS、
审计日志或正式数据保留策略。

仓库另有待同步的 Windmill Gateway 只读工具目录
`f/content_research/research_tools`，包含十项工具：`search_cases`、
`get_case_detail`、`get_hot_videos`、`get_blackhorse_videos`、`search_accounts`、
`get_account_videos`、`get_metric_history`、`get_daily_briefing`、
`get_research_briefs` 与 `get_cost_summary`。它们通过服务器端固定 Resource 读取
规范化数据；本 stdio 服务的五项工具与该 Gateway 目录不是同一部署单元、认证面或
验收证据。

Windmill 会为 granular script scope 额外列出一个受同一路径限制的内置
`runScriptByPath`，因此 Gateway 的协议 surface 是十个业务脚本工具加该内置启动器，
不是十一个任意权限工具。token 的 `read_only` 标志不能开启，因为它会连只读脚本的
job-run 一并拒绝；数据库只读仍由脚本、事务和 reader role 保证。

特别是，`run_deep_analysis` 尚未加入 Gateway。`manual_l3_preview` 和研究台的
审核/预算预览只证明零调用准备度，不能被视为模型执行、预算扣减、Gateway 付费工具
或任何测试服务器部署已完成的证据。
