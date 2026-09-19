# Codex ↔ Windmill MCP V1

日期：2026-09-19

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

只暴露研究工具 folder，而不是整个 workspace：

`f/douyin_research/research_tools`

推荐工具：

只读：
- search_cases
- get_case_detail
- get_hot_signals
- get_blackhorse_videos
- search_accounts
- get_account_videos
- get_metric_history
- get_cost_summary

有成本/写操作：
- refresh_case
- fetch_more_comments
- run_deep_analysis

## Codex Approval

只读工具可较宽松。

会产生收费或写数据的工具：
- approval_mode 建议 prompt / writes
- 明确参数 max_pages / max_items
- 内部仍必须执行 budget guard

不能只依赖 Codex UI 审批作为成本控制。

## 安全

1. MCP token 权限最小化。
2. Scope 限定研究工具 folder。
3. 不暴露 secrets/resources 管理工具给 Codex。
4. 外部付费请求仍从 Provider 层经过 daily_budget。
5. 生产环境 TLS。
6. reverse proxy 不记录 Authorization header。
7. 禁止 URL token，减少浏览器历史/代理日志泄漏风险。

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
