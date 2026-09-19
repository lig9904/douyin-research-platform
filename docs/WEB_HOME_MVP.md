# Web 首页 MVP

日期：2026-09-19

## 范围

当前只实现多平台研究台的“首页总览”，对应已经确认的多平台效果图。

已实现：

- 左侧导航骨架
- 平台分类切换
- 全部平台 / 抖音 / 快手 / 视频号 / 小红书 / B站 / 微博
- 时间窗：24小时 / 7天 / 30天
- 首页当前结果内搜索
- 新增热点 KPI
- 黑马候选 KPI
- 进入 L1 KPI
- API 成本与请求数
- 预算使用率
- 黑马视频表
- 近7天趋势
- 热门关键词
- 重点 Case
- 重点对标账号
- 运行状态
- 历史回看
- IP 适配建议空状态

## 事实优先

首页不使用示例数字代替真实数据。

所有 KPI、黑马、趋势、账号、运行状态都来自 `douyin_research` PostgreSQL。

L2/L3 尚未启用，因此 IP 适配卡明确显示“等待研究证据”，不会自动生成无依据的内容建议。

## Windmill 路径

Raw App：

`f/content_research/research_dashboard`

仓库目录：

`windmill/f/content_research/research_dashboard.raw_app/`

数据库 Resource：

`f/content_research/research_db`

Git 中只保存 Resource 模板：

`windmill/f/content_research/research_db.resource.json`

其中数据库密码引用：

`$var:f/content_research/research_db_password`

真实密码不得提交 Git。

## 数据库连接

当前 Docker Compose 中：

- host: postgres
- port: 5432
- database: douyin_research
- user: douyin_research
- password: Windmill Secret
- sslmode: disable（仅 Docker 内网）

如果以后 PostgreSQL 独立部署，只修改 Windmill Resource，不修改 App 代码。

## 后端

首页只使用一个只读 Backend Runnable：

`backend/get_home_overview.py`

原因：

- 首页一次加载避免连续触发多个 Windmill Job
- 所有 SQL 参数化
- 前端不接触数据库凭据
- 平台筛选在数据库层完成
- 后续详情页再拆独立查询

## 平台规则

`platform=all`：
- 用于全平台数量、成本、研究资产汇总

具体平台：
- 用于黑马、账号、趋势等平台内视图

跨平台原始指标不直接混算黑马分。

当前只有抖音 Provider 为 active；其他平台已注册但未接数据源，因此切换后正常显示空数据。

## 尚未启用

首页上的以下功能暂时禁用，避免产生“按钮能用”的误解：

- 导出日报
- 查看更多
- 查看账号详情

这些入口会在对应页面真正完成后启用。

## 前端栈

- React 19
- Ant Design 6
- CSS
- 原生 SVG 趋势图

没有额外引入大型图表库。

## 自动验证

1. `provider-tests`
   - PostgreSQL 18
   - 真实 Schema
   - 首页 Backend Runnable 实际查询
   - 多平台平台列表
   - 抖音筛选
   - all 聚合

2. `web-dashboard`
   - React/Ant Design 依赖安装
   - esbuild bundle
   - Python Backend 编译

## 下一页

首页通过后，再单独实现：

`视频库 / 黑马视频列表`

不同时铺开账号库、热点库、Case 详情。
