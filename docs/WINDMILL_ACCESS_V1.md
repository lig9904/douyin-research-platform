# Windmill Access Control V1

日期：2026-09-19

## 目标

用户数量不大，因此 V1 不引入额外 IAM、SSO 或 Guest JWT 系统。

采用 Windmill 内建账号 + Folder ACL。

## 角色

### Admin

人数：1–2

权限：
- workspace admin
- secrets/resources
- users/groups
- deployment
- schedules
- production configuration

### Developer

人数：少量开发/研究负责人

权限：
- 编辑 scripts / flows / full-code app
- 访问开发所需资源
- 不默认拥有 workspace admin

### Viewer

Windmill workspace role 使用 Operator，并只给研究台相关 folder 的 read 权限。

权限：
- 打开研究台
- 使用研究台允许的搜索/筛选/标注动作
- 不创建/修改 scripts/flows/apps
- 不授予 variables/resources folder 的读取权限

注意：Operator 并不天然等于“严格只读”，所以必须靠 Folder ACL 限制资源范围。

## Folder

建议统一放：

```text
f/content_research/
  app/
  queries/
  research_tools/
  admin_tools/
```

权限建议：

- Viewer：仅 app + app 必需的只读对象
- Developer：app/queries/research_tools writer
- Admin：admin_tools + resources/secrets

## App Execution

研究台保持 Members 模式。

Backend runnables 由 publisher 身份执行，所以普通 Viewer 不需要 TikHub Token / DB Resource 的直接读取权限。

人工标注时使用：
- `WM_END_USER_EMAIL` = 实际访问者
- 不使用 `WM_EMAIL` 作为标注人，因为后者是 publisher

## Secret Safety

Viewer/Operator 不授予 TikHub / LLM / DB Secret 所在 folder 的 read 权限。

Windmill 文档明确提示：Operator 如果获得 variable 的 read 权限，可通过 API 读取其值，因此资源 ACL 必须严格。

## V1 不使用

- Public anonymous app
- Guest JWT
- SSO
- Service account（Enterprise）
- 复杂多 workspace

## MCP

MCP 只暴露指定研究工具，不暴露所有 workspace scripts。

测试服务器的 workspace-bound MCP token scope 使用一个 `mcp:scripts:` 条目，逗号分隔
下列十个脚本的完整路径；不使用 `*`、`mcp:all`、flow 或 endpoint scope。

当前该 folder 仅包含十项只读工具：`search_cases`、`get_case_detail`、
`get_hot_videos`、`get_blackhorse_videos`、`search_accounts`、
`get_account_videos`、`get_metric_history`、`get_daily_briefing`、
`get_research_briefs`、`get_cost_summary`。MCP 身份只能调用这些工具，不能读取
Resource/Secret、枚举 workspace、访问 App backend、collectors、analysis 或
`admin_tools`。

Windmill 对 granular script scope 会自动增加内置 `runScriptByPath` 工具；该工具仍按
同一 path scope 检查参数，只能运行 `research_tools` 下的脚本。验收应期待十个业务
脚本工具加这个内置启动器，而不是误把它当成跨目录权限。token 不能设置
`read_only=true`，因为 Windmill 会因此禁止所有 job-run；业务只读由精确 scope、
无写脚本、只读事务和数据库 reader role落实。

这些工具在服务端固定使用 `f/content_research/research_db_readonly`，但 MCP token 不应因此
获得 Resource 的 read 权限。应以独立的最小权限数据库 role 作为该 Resource 的
连接身份，且只授予所需表的 `SELECT`；代码内的只读事务是第二道边界，不替代 ACL。

十项入口还会导入 `f/content_research/research_tool_lib/queries`。MCP scope 仍只列出
`research_tools`，但执行身份必须能 view 这个 helper；helper 自身不得进入 MCP
工具列表。测试服务器应给 helper 精确 view 权限，不能把整个 `content_research`
folder 授予 MCP 身份。

收费/写操作必须与只读 folder 和 MCP token 分开。`run_deep_analysis` 当前未开放；
不得把 `manual_l3_preview`、审核或预算预览放入 MCP 作为执行器，也不得由调用参数
升级为付费执行。

测试服务器 ACL 验收至少覆盖：

- MCP token 的 `tools/list` 只展示十项只读业务脚本和受相同 path scope 约束的
  `runScriptByPath`，不展示其他 workspace 或 API endpoint 工具；
- 每项工具能在最小结果集下读取，未知工具/超限输入失败关闭；
- MCP token 不能读取 Resource/Secret、调用收费脚本或运行 collectors/analysis/admin；
- Admin、Developer、Viewer 与 MCP token 的 Folder 权限分别按预期生效。

上述项目已于 2026-09-22 在 `test-research` 测试工作区完成现场验收；新环境、token
轮换或工具清单变化后必须重新执行，不能继承本次证据。
