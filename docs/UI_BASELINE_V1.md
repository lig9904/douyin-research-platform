# V1 研究台 UI 技术基线

日期：2026-09-19

## 1. 框架

Windmill Full-code App + React 19。

使用 Windmill CLI 原生 scaffold：
- wmill app new
- npm install
- wmill app dev
- wmill generate-metadata
- wmill sync push

不单独建立 Next.js/Vite API 项目。

## 2. UI 组件库

采用 Ant Design 6 核心组件。

原因：
- React 19 兼容
- 企业后台场景成熟
- Table / Form / Drawer / Timeline / Tag / Statistic / Tabs 等直接可用
- 中文团队熟悉
- 内部系统不敏感于少量 bundle 体积

暂不采用 @ant-design/pro-components v3：
- 当前仍存在 ESM/CJS 兼容和 ProTable 相关活跃问题
- 我们页面复杂度不需要承担额外依赖风险

## 3. 主要页面与现成组件

今日发现：
- Card
- Statistic
- List
- Tag
- Badge

视频/Case 列表：
- Table
- Input.Search
- Select
- DatePicker/RangePicker
- Segmented
- Pagination

Case 详情：
- Descriptions
- Tabs
- Timeline
- Drawer
- Collapse
- Tag

指标历史：
- 第一版可用简单表格/Statistic
- 确实需要折线后再引入一个轻量图表库

专题/收藏：
- Table/List
- Modal
- Form

成本/任务：
- Statistic
- Progress
- Table
- Alert

## 4. 所有大列表都服务端分页

禁止：
- 一次从 PostgreSQL 拉几万条到浏览器
- 前端全量筛选历史库

后端 SQL 参数：
- limit
- cursor / offset（初期）
- sort
- filters
- search

默认 page size 20–50。

数据大后优先改 keyset/cursor pagination，而不是无限增大 OFFSET。

## 5. 搜索

顶部全局搜索调用 backend/search_cases.pg.sql。

V1：
- title/description substring
- nickname
- tags
- statuses
- date range
- transcript keyword（已有时）

搜索结果显示命中来源，例如：
- 标题
- Transcript
- 人工备注
- 标签

语义搜索后置。

## 6. 前端不持有 Secret

React 端只能调用 wmill.ts 暴露的 backend runnable。

禁止：
- TikHub token 放浏览器
- PostgreSQL connection string 放浏览器
- ASR key 放浏览器

## 7. Backend runnable 分工

SQL 查询：
- get_today.pg.sql
- list_cases.pg.sql
- search_cases.pg.sql
- get_case.pg.sql
- get_metric_history.pg.sql
- list_accounts.pg.sql
- get_cost_summary.pg.sql

写操作：
- save_annotation.ts
- add_to_collection.ts
- update_case_status.ts

外部服务：
- Python workspace scripts，不直接作为复杂 inline frontend runnable

## 8. 用户体验原则

“人人看懂”优先于暴露所有技术字段。

默认显示：
- 为什么被发现
- 当前表现
- 增长
- 研究状态
- 简要结论

高级/原始信息收在：
- 展开区
- Raw Data Tab
- 技术信息 Tab

不要把 JSON、endpoint、rule version 放首页。

## 9. URL 与访问

V1 使用固定研究台 URL 供团队收藏。

Community Edition 不依赖 Enterprise 的 Workspace Default App。

普通人员使用 Operator 账号，只访问研究 App，不编辑代码。
