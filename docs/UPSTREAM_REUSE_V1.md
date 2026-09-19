# Upstream Reuse Plan

日期：2026-09-19

## TikHub Official Plugin

Repository:
https://github.com/TikHub/tikhub-plugin

License:
MIT (Copyright 2026 TikHub)

## 决策

不把 TikHub Plugin 当成生产运行时依赖。

原因：
- 它面向通用 Agent 交互
- 多平台 Skill 较泛化
- Douyin platform skill 本身将 Billboard / Creator / Index 等高级面 de-scope 到 endpoint discovery
- 我们需要稳定的数据库、预算、历史快照和分层漏斗，而不是每次由 Agent 临时决定调用

但复用其经过官方整理的“执行原则”。

## 直接采用的工程模式

### 1. Cost-first

任何大分页/批量拉取先：
- calculate_price
- daily_usage
- 预计 pages/calls
- budget guard

生产系统不要求用户每次人工批准，但必须满足 daily_budget。

### 2. Pagination hard cap

任何 cursor endpoint 都必须同时满足：
- has_more / next cursor
- AND max_pages
- AND max_items
- AND daily_budget

不能只依赖 has_more。

### 3. Dedup by stable ID

边翻页边按：
- aweme_id
- sec_user_id
等稳定平台 ID 去重。

### 4. Fair benchmark window

多个对标账号比较必须统一：
- 相同样本量上限
- 相同时间窗
- 相同指标定义

若某账号样本不足，必须显式记录。

### 5. Verification gate

采集结果进入下一阶段前先验证：
- 返回非空
- identity field 可解析
- 时间窗/地区/筛选条件实际生效
- 指标范围合理
- pagination 没卡死

### 6. Sample disclosure

评论/账号分析必须保存：
- 总量（若可得）
- 实际分析样本量
- 页数
- 采样策略

不能把几十条评论表述成整站总体意见。

## 不直接采用

以下通用 Skill 结论不能直接作为本项目方法论：

- “粉丝数高 = 强”
- 简单 engagement rate 作为账号优劣总判断
- Agent 临时决定无限深挖
- 通用 sentiment 作为评论研究主结论
- 单个爆款视频直接推导因果

这些需要使用本项目自己的：
- blackhorse rules
- DCI / Case
- Pattern evidence
- counter-case
- internal benchmark

## Attribution

如后续直接复制 TikHub Plugin 的 substantial code/documentation：
- 保留 MIT copyright/license notice

仅复用通用工程思想时，不需要把其 Skill 当运行时依赖。
