# Multi-platform Foundation V1

日期：2026-09-19

## 目标

在继续 Web 之前，把“平台”从抖音专用实现中抽成整个项目的一等维度。

这不是现在接入所有平台，而是保证第二个平台接入时：
- 不改数据库主键语义
- 不重写 L0/L1
- 不重写搜索/Case/收藏
- 不重写 Web 导航与筛选
- 不把不同平台指标混在同一评分 cohort

## 平台注册表

`platform_registry` 当前预置：

- douyin / 抖音：enabled=true, active
- kuaishou / 快手：disabled, planned
- wechat_channels / 视频号：disabled, planned
- xiaohongshu / 小红书：disabled, planned
- bilibili / B站：disabled, planned
- weibo / 微博：disabled, planned

“出现在 UI”与“已接生产 Provider”分开。

## Canonical Identity

账号：
`platform + platform_account_id`

视频：
`platform + platform_video_id`

同一个字符串 ID 出现在两个平台时，是两个不同实体。

数据库不再给 platform 隐式默认值，所有新数据必须显式声明平台。

## Provider Contract

业务层依赖：

`PlatformResearchProvider`

关键属性：

- `provider_name`
- `platform_name`
- `capabilities`

关键能力：

- `discover(kind, ...)`
- `search_videos(...)`
- `fetch_videos(...)`
- `fetch_account_posts(...)`

TikHub 抖音实现：

`TikHubDouyinProvider`

兼容旧代码：

`TikHubProvider = TikHubDouyinProvider`

## L0/L1

每个采集 Run 明确记录一个 `platform`。

L0/L1 Runner：
- 从 Provider 读取 `platform_name`
- 验证返回数据的平台一致
- observation fingerprint 包含 platform
- pipeline_run 写 platform
- 同一个 run 不跨平台评分

未来跨平台总览属于展示/统计层，不是把抖音和快手原始指标直接混算。

## Web 后续

所有页面查询都从一开始支持：

`platform=all|douyin|kuaishou|...`

但只有 enabled/active 平台真正展示数据。

首页平台切换：
- 全部平台
- 抖音
- 快手
- 视频号
- 小红书
- B站
- 微博

“全部平台”用于汇总数量、成本、研究资产；单个平台内做指标排名和黑马评分。

## 不做

本阶段不做：
- 快手真实 API
- 视频号真实 API
- 小红书真实 API
- B站真实 API
- 微博真实 API
- 跨平台统一爆款分数
- 仓库重命名
- Python package 重命名

现有 `douyin_research` 仅作为历史技术命名，不再代表产品只能研究抖音。
