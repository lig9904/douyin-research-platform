# Deterministic Scoring & Promotion V1

日期：2026-09-19

## 目标

用代码完成：
- 黑马候选排序
- 热点优先级
- 账号异常爆发检测
- L0 → L1 → L2 → L3 晋级

不调用 LLM。

## 设计原则

1. 不用一个“神秘AI分数”。
2. 尽量使用同时间窗、同垂类、同来源内的 percentile，而不是写死绝对阈值。
3. 缺失字段不记 0，也不直接扣分。
4. 每个 score 都保存 components + rule_version。
5. “高分”只是研究优先级，不代表视频质量或因果结论。

---

## 1. L0：发现

来源：
- low-follower viral board
- high completion
- high follower growth
- high likes
- rising hot/search/topic
- Creator hot spot/topic/material
- structured search
- watched accounts

只做：
- canonical dedup
- discovery_event
- source metadata
- first snapshot

不做 LLM。

---

## 2. Feature Set

### A. source_strength

榜单/来源内部位置：

```text
source_rank_percentile
```

排名越靠前越高。

不同榜单不直接拿原始 rank 横向比较，先转成各自 percentile。

### B. recency

```text
age_hours = now - published_at
```

不是简单“越新越高”。

按研究模式分别处理：

- realtime: 0–72h
- weekly: 0–7d
- evergreen: 不使用 recency 惩罚

### C. multi_signal

同一视频在一个时间窗内命中多少独立信号：

```text
low_fan
high_like
high_completion
rising_topic_related
creator_material
search
watched_account
...
```

同时命中多个来源是强证据，但来源之间有相关性，因此只做小幅 bonus，不线性累加成巨大分数。

### D. metric_velocity

有 >=2 个快照时计算：

```text
like_velocity    = Δlikes / hours
comment_velocity = Δcomments / hours
share_velocity   = Δshares / hours
play_velocity    = Δplays / hours   (只有数据可用时)
```

再在同 cohort 内转 percentile。

### E. follower_efficiency

避免“低粉高赞”简单除法在极低粉账号上爆炸。

建议：

```text
engagement = likes + comments + shares
efficiency_raw = log1p(engagement) - log1p(max(followers, floor))
```

再在同 cohort 内做 percentile。

具体 floor / engagement 组合属于版本化规则。

### F. account_outlier

账号已有足够历史样本时：

```text
baseline = median(last N comparable posts)
outlier_ratio = current_metric / max(baseline, floor)
```

至少 N>=5 才计算。

优先用 median，不用 mean，避免账号以前的一个超级爆款把基线抬高。

可分别计算：
- like_outlier
- share_outlier
- comment_outlier
- play_outlier（如有）

### G. rank_acceleration

对热点/signal：

```text
rank_delta / elapsed_time
heat_delta / elapsed_time
```

用 signal_snapshot 计算。

---

## 3. Cohort

Percentile 必须在可比较集合中计算。

默认 cohort key：

```text
source_type
+ time_window
+ category/profile
+ capture_date
```

如果某 cohort 样本太少：
- 向上回退到 source_type + time_window
- 仍太少则只展示 raw feature，不参与 composite score

---

## 4. Composite Priority

V1 不用复杂 ML。

先将可用组件归一成 0–1 percentile：

```text
P = [
  source_strength,
  metric_velocity,
  follower_efficiency,
  account_outlier,
  rank_acceleration
]
```

基础分：

```text
base = median(available P)
```

使用 median 而不是 weighted sum：
- 对异常值更稳健
- 缺失部分指标时更自然
- 不需要假装知道“点赞权重应该0.23还是0.31”

小幅 bonus：

```text
multi_signal_bonus = bounded function(unique_signal_count)
watchlist_bonus    = small fixed bonus if manually watched
```

最终：

```text
priority_score = clamp(base + bonuses, 0, 1) * 100
```

所有 bonus 必须版本化。

---

## 5. Data Confidence

另算一个完全独立的 `data_confidence`，不要和 score 混在一起。

示例：

### LOW
- 只有一个 discovery event
- 只有一个 snapshot
- 无账号基线

### MEDIUM
- >=2 snapshots
- 或多个独立 discovery source

### HIGH
- 多 snapshots
- 账号基线充分
- 多来源交叉命中

所以 UI 显示：

```text
研究优先级：87
数据完整度：中
```

而不是“AI认为87%会爆”。

---

## 6. Promotion

### L0 → L1

自动。

条件：
- canonical entity 成功
- 未命中 exclude rule
- 数据最小字段合格

### L1 → L2

按“每日/每垂类预算槽位”晋级，而不是固定全局分数。

例如配置：

```yaml
daily_l2_slots:
  drama: 20
  myth_culture: 20
  travel: 20
  visual: 10
  general: 20
```

按 priority_score 取 Top-N。

这样每天数据暴增时，成本不会跟着无限增长。

### L2 → L3

更严格：
- Top-N
- 或人工标记重点
- 或多信号 + 高增长
- 或需要 Pattern 对照研究

L3 每日/每周有独立预算。

---

## 7. Exclusion Rules

纯代码排除/降权：

- duplicate/repost（可确认时）
- video unavailable
- 广告/抽奖明显标识（先标签，不一定硬删）
- 明星/超大号事件效应标签
- 发布时间过老且不属于 evergreen 模式
- 数据异常/字段明显错误
- 自己账号内容（若研究任务需要排除）

这些规则输出 reason code，不静默丢弃：

```text
EXCLUDED_DUPLICATE
EXCLUDED_UNAVAILABLE
DOWNRANK_PROMOTION
TAG_LARGE_ACCOUNT_EFFECT
...
```

---

## 8. SQL 优先

可在 PostgreSQL 完成：
- median / percentile
- rank
- daily cohort
- multi-source count
- snapshot delta
- account baseline
- Top-N promotion

Python 只处理：
- 较复杂 robust statistics
- 聚类/embedding（L2以后）
- Provider normalization

---

## 9. Versioning

规则版本：

```text
blackhorse-v1.0.0
promotion-v1.0.0
account-baseline-v1.0.0
```

每条 video_score 保存：
- score
- rule_version
- components JSON
- calculated_at

规则升级后可以从历史 snapshot 重算，不重新购买 API 数据。

---

## 10. 不做

V1 不做：
- 训练机器学习“爆款预测器”
- LLM 判断黑马
- 固定“点赞超过X就是爆款”
- 一个全局分数横跨所有垂类
- 把 score 当成爆火概率
