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
f/douyin_research/
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

建议 MCP scope 对应：
`f/douyin_research/research_tools`

收费工具与只读工具分开命名和路径。
