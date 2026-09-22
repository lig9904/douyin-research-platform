# Codex ↔ Windmill MCP V1

日期：2026-09-20

## 结论

Windmill 作为 MCP Server 暴露研究工具给 Codex。

Community Edition 可使用 Windmill 开源 MCP server 路由；官方价格页注明免费自托管包含所有开源功能，MCP 官方文档同时给出 self-hosted gateway URL。

不需要自研 MCP Server。

## 当前交付状态（2026-09-22）

- 已完成：本机 stdio MCP 五项只读查询；Windmill Gateway 十项只读研究工具；参数上限、只读事务、跨目录拒绝；测试服务器独立 reader role、固定 Resource、十个精确 path scope、`mcp_disable_token_query_param`、HTTPS、Codex OAuth、十工具调用和越权失败烟测。
- 客户端证据：Codex CLI 使用 OAuth 安全凭据完成一次真实 `get_research_briefs(limit=1)` 调用并返回 `MCP_CODEX_SMOKE_OK`；项目配置隐藏内置 `runScriptByPath`。
- 发布判断：测试服务器 MCP Gateway 已可用于只读研究查询，不再是正式 V1 发布阻点；token 轮换、工具清单或数据库授权变化后必须复验。
- 查询边界：只查询已经进入研究库的规范化数据，不是抖音全站或互联网实时搜索；可返回公开标题和账号昵称，不返回原评论、完整转写、描述、URL、Provider 原始载荷或凭据，不开放付费 L3 执行。研究台保留按视频 ID 查看原始记录的页面下钻。

## 认证

优先使用 HTTP Authorization Bearer，不把 token 放在 URL query string。

仓库使用项目级、无密钥配置：

```toml
[mcp_servers.douyin_research]
url = "https://dy.yudao.cc:6443/api/mcp/w/test-research/mcp"
enabled = true
required = false
default_tools_approval_mode = "approve"
disabled_tools = ["runScriptByPath"]
```

Codex 当前使用 OAuth，凭据由 Codex 安全存储，不写入项目配置。非交互诊断 token 仅存
服务器 root-only 文件和 Windmill token store，30 天到期。所有 token：

- 绑定 `test-research`，`super_admin=false`
- scope 只含十个完整脚本路径，不含通配符、`mcp:all`、flow 或 endpoint
- 仅存本地安全凭据存储 / secrets
- 不提交 GitHub
- 不写到 URL
- 不写进 README 示例真实值

`mcp_disable_token_query_param` 已开启并读回为 `true`；URL `?token=` 实测返回 401。

## Tool Scope

业务脚本 scope 用一个 `mcp:scripts:` 条目逗号分隔下列十个完整路径；不授权整个
workspace，也不使用 `research_tools/*` 目录通配符。

仓库已定义以下十项只读工具。它们只读取已经入库的规范化数据，参数有
schema 上限，查询在显式只读事务中执行；不会刷新缓存、调用 Provider、预占
预算或返回 URL、正文、转写、评论原文、Provider 原始载荷或凭据：

- search_cases
- get_case_detail
- get_hot_videos
- get_blackhorse_videos
- search_accounts
- get_account_videos
- get_metric_history
- get_daily_briefing
- get_research_briefs
- get_cost_summary

工具使用服务器端固定的 PostgreSQL Resource
`f/content_research/research_db_readonly`；它绑定独立的
`douyin_research_mcp_reader` 数据库身份，不能复用应用写入身份。调用者不能提交
DSN、数据库用户名、排序字段、
表名或 Provider 参数。`get_blackhorse_videos` 返回记录中的版本化优先级评分和
`rule_version`，默认门槛是 `min_score=60`，调用者可仅在 `0..100` 内收紧它。

`run_deep_analysis` **尚未开放**，也不在这十项 Gateway 工具中。现有
`manual_l3_preview`、研究台审核和预算预览均是零调用流程，不能因为传入
`execute` 或其他参数而变成付费执行器。未来若开放，必须独立放在受限 folder，
复用审批指纹绑定、服务端固定模型/价格配置、daily_budget、幂等 task key 和
精确付费确认；不得把模型、价格、API Key、证据正文或预算值开放给 MCP 调用方。

## Codex Approval

只读工具可较宽松。

当前 Gateway 只有只读工具，适用只读审批模式。未来产生收费或写数据的工具必须：

- 单独配置为 prompt / writes；
- 有明确、有限的业务参数；
- 在服务端继续执行预算闸门，不能只依赖 Codex UI 审批。

不能只依赖 Codex UI 审批作为成本控制。

注意：Windmill 的 token `read_only` 开关会同时禁止 job-run，因此这个 MCP token
不能设为 `read_only=true`；否则十个脚本均无法执行。这里的“只读”由精确
`mcp:scripts:` scope、无写入口、显式 `BEGIN READ ONLY` 和最小权限数据库角色共同
保证。Windmill 还会为 granular script scope 自动列出内置 `runScriptByPath`；它受
同一 path scope 约束，只能运行上述目录，不能借此运行其他脚本。

十个入口会导入 `f/content_research/research_tool_lib/queries`。MCP scope 不应把该
helper 暴露为工具，但执行身份仍需对 helper 有 view 权限，并需能在 job 内读取固定
只读 Resource。测试服务器应单独验证这个执行 ACL，不能通过扩大到整个
`f/content_research/*` 来绕过。

## 本机 Gateway 证据（2026-09-20）

已在 `content-research-local` 的 Windmill CE v1.815.0 同步并实际验证：

- 使用 15 分钟、workspace-bound、仅含
  `mcp:scripts:f/content_research/research_tools/*` 的临时 token；
- `initialize` 返回 MCP `2025-03-26` 和 tools capability；
- 当时的 `tools/list` 返回七个业务脚本及受同 scope 约束的内置 `runScriptByPath`；
- 当时七个业务脚本均经 `tools/call` 成功完成最小结果集调用；新增三项不继承该证据；
- `limit=51` 返回固定 `MCP_INPUT_INVALID`；
- 用 `runScriptByPath` 尝试运行
  `f/content_research/analysis/manual_l3_preview` 被明确拒绝为不在 token scope；
- 无 Provider/LLM/付费调用，临时 token 已撤销，临时凭据文件已删除。

这只证明本机单用户 Gateway、脚本 import 和 path scope；本机 Resource 仍使用开发库
身份，不证明测试服务器的独立 reader role、多账号 Folder ACL、TLS、反代日志脱敏、
审计保留或正式部署。

## 安全

1. MCP token 权限最小化。
2. Scope 限定研究工具 folder。
3. 不暴露 secrets/resources 管理工具给 Codex。
4. 外部付费请求仍从 Provider 层经过 daily_budget。
5. 生产环境 TLS。
6. reverse proxy 不记录 Authorization header。
7. 禁止 URL token，减少浏览器历史/代理日志泄漏风险。

## 测试服务器接入与验收

2026-09-22 已在 `test-research` 完成：

1. HTTPS URL 为 `https://dy.yudao.cc:6443/api/mcp/w/test-research/mcp`。
2. Codex 使用 OAuth 安全凭据；诊断脚本使用 Authorization Bearer。token 未进入 URL、
   Git、截图或证据文件。
3. Windmill 已开启 `mcp_disable_token_query_param`；两个 workspace-bound token 均只获
   十个完整脚本 path scope，且 `super_admin=false`，不含 `*`、`mcp:all` 或 endpoint。
4. `tools/list` 出现上述十项业务脚本和 Windmill 内置、受相同 path scope 约束的
   `runScriptByPath`，未出现其他业务脚本或 API endpoint；分别调用一次
   `search_cases`、`get_case_detail`、`get_hot_videos`、`get_blackhorse_videos`、
   `search_accounts`、`get_account_videos`、`get_metric_history`、`get_daily_briefing`、
   `get_research_briefs`、`get_cost_summary` 的小结果集。
5. 未知工具、`limit=51`、跨目录 `manual_l3_preview`、Resource、Secret、无 Bearer
   和 URL token 均失败关闭。Codex 项目配置再隐藏 `runScriptByPath`。

服务器脱敏证据：

- `/srv/douyin-research-test/evidence/mcp-positive-20260922.json`
- `/srv/douyin-research-test/evidence/mcp-negative-20260922.json`
- `/srv/douyin-research-test/evidence/windmill-mcp-query-push-6e309213.log`

上述 smoke 还应记录运行时间、调用工具、结果条数和固定错误码，不记录正文、token、
SQL、数据库连接串或 Provider 原始响应。真实多账号 Folder ACL、TLS、审计保留和
Gateway 协议互操作性仍应在每个新目标环境单独验收。

## Community / Enterprise 边界

已确认企业限制主要在：
- Audit logs
- global concurrency
- advanced observability
- Git Sync >2-user workspace
等。

MCP Server 本身位于开源 Windmill backend 中，官方自托管 MCP 文档没有将其标为 Enterprise-only。

注意区分：
- Windmill **作为 MCP Server** 给 Codex：本方案使用。
- Windmill **作为 MCP Client** 去登录某些外部 MCP OAuth server：源码中部分 OAuth discovery 能力有 OSS/EE 差异，与本方案不是同一方向。
