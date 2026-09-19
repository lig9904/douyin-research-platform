# Metric Snapshot Strategy V1

日期：2026-09-19

## 目标

在尽量低成本的前提下记录“视频正在怎么涨”，而不是给所有视频高频购买全量统计。

## 核心判断

不同 TikHub endpoint 能提供的指标和价格不一样。

因此 metric snapshot 不采用“一种接口覆盖全部视频”的做法，而是分级：

### L0：只保存发现时指标

来源：
- Billboard
- Creator
- Index/Search

凡来源已经返回的指标直接落库。

不为 L0 视频额外调用统计接口。

### L1：廉价候选跟踪

优先：
- `fetch_multi_video_v2` batch detail
- 最多按 provider 实测上限批量

用途：
- 补元数据
- 补可用互动指标
- 确认视频可用状态

如果实测证明普通 batch detail 不稳定返回 play_count，则 L1 不为了播放量单独购买昂贵 stats。

### L2：重点候选播放量跟踪

只有需要判断“增长速度”的 shortlist 才调用：
- `fetch_multi_video_statistics`

在 V0 确认其：
- max IDs
- price
- play_count 稳定性

之后再决定跟踪频率。

建议初始候选：
- 发现时
- +6h
- +24h
- +72h

不是所有视频都四次追踪。

### L3：核心 Case

必要时进一步使用：
- TikHub item trends
- 更多账号上下文

只用于少量深研。

## 指标缺失规则

`NULL != 0`

必须区分：
- 真实值 0
- provider 未返回
- 请求失败
- 视频不可用

## 增长计算

全部代码计算：

```text
absolute_growth = current - previous
growth_rate = (current - previous) / max(previous, floor)
velocity = growth / elapsed_hours
relative_to_account_baseline = current / account_recent_median
```

不使用 LLM。

## 动态停止跟踪

满足任一条件停止高频追踪：
- 增长速度连续两个窗口明显下降
- 已超过最大跟踪年龄
- 被人工标记为不研究
- 视频不可用
- 当日 API 预算触顶

## 批量调度

不要“每个视频一个 schedule”。

做统一 snapshot scheduler：

```text
select due videos
↓
按 endpoint + priority 分组
↓
batch 到最大安全数量
↓
budget guard
↓
request
↓
save snapshot
↓
计算 next_due_at
```

## 成本策略

V0 没确认 `fetch_multi_video_statistics` 官方实际价格前：
- 不将其设为 L1 默认调用
- 不假设它和普通 batch detail 同价

系统通过 endpoint registry / price API 动态读取价格，不在业务代码写死。
