# Web 研究台 V1

日期：2026-09-19

## 范围

当前实现多平台研究台的本机 V1，不再局限于早期“首页总览”原型。

已实现：

- 顶部响应式导航
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
- 视频库、账号库、热点库与全局搜索
- 视频指标时间线与运行/成本只读总览
- L3 待审摘要、一次性本机正文审核、审核保存与零调用预算预览
- 六个平台的本地品牌 SVG 图标

## 事实优先

首页不使用示例数字代替真实数据。

所有 KPI、黑马、趋势、账号、运行状态都来自 `douyin_research` PostgreSQL。

L2 证据和 L3 人工审核准备链路已可在合成候选上验证；正式 L3 Provider 仍失败关闭，因此不会自动生成无依据的内容建议或发生付费调用。

## Windmill 路径

Raw App：

`f/content_research/research_dashboard`

仓库目录：

`windmill/f/content_research/research_dashboard.raw_app/`

数据库 Resource：

`f/content_research/research_db`

Git 中只保存 Resource 模板：

`windmill/f/content_research/research_db.resource.yaml`

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

首页仍使用聚合只读 Backend Runnable：

`backend/get_home_overview.py`

其他页面按领域拆分独立只读 runnable，L3 审核另有 prepare/approve/budget 三个后端。这样保持：

- 首页一次加载避免连续触发多个 Windmill Job
- 所有 SQL 参数化
- 前端不接触数据库凭据
- 平台筛选在数据库层完成
- 详情、搜索、时间线和运行总览各自可独立验收

## 平台规则

`platform=all`：
- 用于全平台数量、成本、研究资产汇总

具体平台：
- 用于黑马、账号、趋势等平台内视图

跨平台原始指标不直接混算黑马分。

当前只有抖音 Provider 为 active；其他平台已注册但未接真实数据源，因此切换后正常显示空数据。平台标识来自本地 vendored SVG，不依赖第三方 CDN。

## 尚未启用

首页上的以下功能暂时禁用，避免产生“按钮能用”的误解：

- 导出日报
- 真实 L3 模型执行
- 多平台 Provider 采集

视频、账号和热点的监测/专题，以及视频收藏和保存筛选已使用 actor 绑定、幂等、零外呼的
PostgreSQL 后端启用；边界和验收见 `docs/WINDMILL_RESEARCH_ACTIONS_V1.md`。

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

## 后续范围

本机阶段已完成一次受硬上限约束的 TikHub 单调用小额 smoke；剩余工作是 GitHub CI 和发布证据。测试服务器阶段再验证多账号 Folder ACL、TLS/反代、正式 Secret 注入和真实 Provider 预算审计。
