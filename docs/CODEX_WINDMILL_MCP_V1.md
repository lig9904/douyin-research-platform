# Codex ↔ Windmill MCP V1

日期：2026-09-20

## 结论

Windmill 作为 MCP Server 暴露研究工具给 Codex。

Community Edition 可使用 Windmill 开源 MCP server 路由；官方价格页注明免费自托管包含所有开源功能，MCP 官方文档同时给出 self-hosted gateway URL。

不需要自研 MCP Server。

## 认证

优先使用 HTTP Authorization Bearer，不把 token 放在 URL query string。

Codex 支持：

```toml
[mcp_servers.douyin_research]
url = "https://windmill.example.com/api/mcp/w/<workspace>/mcp"
bearer_token_env_var = "WINDMILL_MCP_TOKEN"
enabled = true
```

Token：
- 仅存本地环境变量 / secrets
- 不提交 GitHub
- 不写到 URL
- 不写进 README 示例真实值

Windmill 自身已有 `mcp_disable_token_query_param` 设置，应在部署后开启，拒绝 URL 中的 `?token=`。

## Tool Scope

业务脚本 scope 只覆盖研究工具 folder，而不是整个 workspace：

`mcp:scripts:f/content_research/research_tools/*`

仓库已定义以下七项只读工具。它们只读取已经入库的规范化数据，参数有
schema 上限，查询在显式只读事务中执行；不会刷新缓存、调用 Provider、预占
预算或返回 URL、正文、转写、评论原文、Provider 原始载荷或凭据：

- search_cases
- get_case_detail
- get_hot_videos
- get_blackhorse_videos
- search_accounts
- get_account_videos
- get_metric_history

工具使用服务器端固定的 PostgreSQL Resource
`f/content_research/research_db`；调用者不能提交 DSN、数据库用户名、排序字段、
表名或 Provider 参数。`get_blackhorse_videos` 返回记录中的版本化优先级评分和
`rule_version`，默认门槛是 `min_score=60`，调用者可仅在 `0..100` 内收紧它。

`run_deep_analysis` **尚未开放**，也不在这七项 Gateway 工具中。现有
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
不能设为 `read_only=true`；否则七个脚本均无法执行。这里的“只读”由精确
`mcp:scripts:` scope、无写入口、显式 `BEGIN READ ONLY` 和最小权限数据库角色共同
保证。Windmill 还会为 granular script scope 自动列出内置 `runScriptByPath`；它受
同一 path scope 约束，只能运行上述目录，不能借此运行其他脚本。

七个入口会导入 `f/content_research/research_tool_lib/queries`。MCP scope 不应把该
helper 暴露为工具，但执行身份仍需对 helper 有 view 权限，并需能在 job 内读取固定
Resource。测试服务器应单独验证这个执行 ACL，不能通过扩大到整个
`f/content_research/*` 来绕过。

## 本机 Gateway 证据（2026-09-20）

已在 `content-research-local` 的 Windmill CE v1.815.0 同步并实际验证：

- 使用 15 分钟、workspace-bound、仅含
  `mcp:scripts:f/content_research/research_tools/*` 的临时 token；
- `initialize` 返回 MCP `2025-03-26` 和 tools capability；
- `tools/list` 返回七个业务脚本及受同 scope 约束的内置 `runScriptByPath`；
- 七个业务脚本均经 `tools/call` 成功完成最小结果集调用；
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

下面是测试服务器的目标配置，不是已部署或已验证的 Gateway 声明：

1. 使用 HTTPS URL：
   `https://<windmill-fqdn>/api/mcp/w/<workspace>/mcp`。
2. Codex 从本机环境变量读取 `WINDMILL_MCP_TOKEN`，通过
   `Authorization: Bearer ...` 发送；不把 token 放入 URL、Git、截图或日志。
3. Windmill 开启 `mcp_disable_token_query_param`，反向代理对 Authorization header
   脱敏，workspace-bound token 仅获
   `mcp:scripts:f/content_research/research_tools/*` scope，且不使用全局
   `mcp:all` / workspace 全权 token。
4. 以 MCP token 执行 `tools/list`，确认只出现上述七项业务脚本和 Windmill 内置、
   受相同 path scope 约束的 `runScriptByPath`，不出现其他业务脚本或 API endpoint；分别调用一次
   `search_cases`、`get_case_detail`、`get_hot_videos`、`get_blackhorse_videos`、
   `search_accounts`、`get_account_videos`、`get_metric_history` 的小结果集。
5. 验证未知工具、超限参数、试图传入 Resource/Provider/执行参数均失败关闭；同时
   验证 token 无法列出或运行 collectors、analysis、admin、resources、secrets。

上述 smoke 还应记录运行时间、调用工具、结果条数和固定错误码，不记录正文、token、
SQL、数据库连接串或 Provider 原始响应。真实多账号 Folder ACL、TLS、审计保留和
Gateway 协议互操作性必须在目标测试服务器单独验收。

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
