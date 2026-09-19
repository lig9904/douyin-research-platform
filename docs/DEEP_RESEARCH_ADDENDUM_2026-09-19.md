# 深挖补充：实施修正

日期：2026-09-19

## 1. TikHub API 主域名修正

TikHub 官方 Plugin README 曾写“中国用户可切换 api.tikhub.dev”。

但 TikHub 当前 API 文档首页更新后的明确建议是：

- Primary domain: https://api.tikhub.io
- 避免优先使用 api.tikhub.dev，因为可能出现更慢响应和性能下降
- 如果请求超过 30 秒，优先从部署网络/proxy 侧解决

因此 V1 统一使用：

```text
https://api.tikhub.io
```

除非真实压测证明特定部署网络下另一域名更优。

## 2. 用户访问方式修正

V1 不优先做 Guest App。

Guest 没有 Windmill 账号，但必须：

- 通过 SSO/OAuth/SAML 登录
- 或由我们自己的 backend 签 guest JWT

这会额外引入身份系统开发。

V1 团队规模在 Community Edition 合理范围内时：

- 手工创建 Windmill 用户
- 普通成员设 Operator
- 开发成员设 Developer
- 研究台保持 Members / publisher execution mode

这是开发量最低的路径。

## 3. App publisher 权限

Full-code App 默认 backend runnable 使用 publisher 权限执行。

安全设计：

- 不用 superadmin 发布研究台
- 使用权限受限普通用户
- 只允许访问：
  - douyin_research PostgreSQL Resource
  - f/douyin_research 下必需 scripts/flows
  - 必需 secrets
- App 自动生成 policy 只允许触发 App 引用的 runnable
- 开启 sandbox isolation

人工操作实际用户通过：

`WM_END_USER_EMAIL`

记录。

## 4. 普通员工角色

Windmill Operator：

- 可以查看和执行已授权的 App / scripts / flows
- 不能创建、编辑、删除和预览代码

非常适合普通研究台用户。

Community Edition 的 Workspace Default App 是不可用的企业功能，因此不能依赖“员工登录后自动进入研究台”。

V1 只需提供固定研究台 URL / 浏览器书签。

## 5. 数据库访问进一步简化

Windmill Community Edition 原生支持 PostgreSQL 作为脚本语言。

因此研究台大多数查询直接使用：

```text
backend/*.pg.sql
```

而不是：

```text
React -> 自研 API -> ORM -> PostgreSQL
```

优点：

- 参数化查询
- 少一层 API
- 少一套 ORM
- 少一套数据模型
- Codex 更容易维护
- 前端通过自动生成 wmill.ts 调用即可

## 6. Windmill CLI 的安全同步默认值

`wmill sync` 默认：

- skipSecrets: true
- includeSchedules: false
- includeTriggers: false

这符合我们的 GitHub source-of-truth 设计。

生产部署需要明确决定是否把 schedule/trigger 纳入版本控制，不要无意依赖 UI 中手工配置而丢失。

## 7. TikHub SDK / OpenAPI

TikHub 官方 Python SDK：

- 当前 reference 来自 OpenAPI V5.3.2
- 1010 endpoints
- Douyin App V3 47 endpoints
- Douyin Billboard 31 endpoints
- SDK 和 endpoint 参数 1:1 对应 OpenAPI

因此 Provider 实现不需要自己维护 URL 常量全集。

只封装 V1 真正使用的少量 method 即可。

## 8. TikHub Search 与 Index 不是同一用途

官方 Douyin Skill 推荐日常关键词搜索优先专用 Search series：

`/douyin/search/...`

Index 更适合作为：

- 带“低粉爆款/高完播/高涨粉/高点赞”标签的结构化研究库
- 垂类/时长/发布日期筛选

所以 V1 Provider 应同时保留：

- Billboard：自动发现
- Index：研究型筛选
- Search：关键词搜索
- App V3：详情和评论

而不是用 Index 替代全部搜索。

## 9. 分页必须受成本上限控制

TikHub 官方 Skill 自己也明确：

- 每页都计费
- 必须 cap pages

V1 每个分页函数必须具备：

```text
max_pages
max_items
max_cost_usd
```

至少一个硬上限。

Agent 不得直接拥有“翻到 has_more=false”的无限权限。

## 10. 可直接借用 Windmill Hub，但只借通用件

Windmill Hub 已有 verified PostgreSQL building blocks：

- Execute Query
- TLS PostgreSQL
- Backup PostgreSQL to S3

并有现成 Error Handler 示例。

这些属于通用基础设施，可按需 fork。

但研究业务逻辑（黑马评分、采集、Pattern/Case）仍自己写，避免把核心逻辑分散在第三方社区脚本里。
