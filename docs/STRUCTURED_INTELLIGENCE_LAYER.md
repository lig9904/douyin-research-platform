# Structured Intelligence Layer

## 定位

在 Raw Data 与 LLM Research 之间增加一个明确的“结构化 Intelligence 层”。

```text
Raw TikHub
  ↓
Normalize
  ↓
Structured Intelligence
  ├─ Billboard signals
  ├─ Index trends
  ├─ Creator Center trends
  ├─ snapshots
  ├─ statistics
  └─ deterministic scores
  ↓
Evidence Package
  ↓
LLM only if needed
```

## 为什么要单独一层

如果没有这一层，Agent 很容易：
- 看到100条视频后自己总结“最近都在做什么”
- 看到数字后用自然语言猜趋势
- 看到评论后直接归因

但 TikHub / Index 已经提供大量结构化平台统计。

所以 LLM 的输入应是：
- 已计算趋势
- 已对齐时间窗口
- 已归一化指标
- 已抽代表样本

而不是原始数据堆。

## Intelligence Snapshot

建议后续增加通用 snapshot 概念：

- intelligence_type
- scope_type (keyword/category/account/video/city)
- scope_key
- period_start
- period_end
- provider
- endpoint
- payload
- normalized_metrics
- captured_at

适合保存：
- keyword trend
- category consume trend
- duration distribution
- portrait
- hot topics
- related words

## 周报生成

周报不再从“视频池全部喂给模型”开始。

正确顺序：

```text
SQL + Index + Billboard
  ↓
weekly facts JSON
  ↓
规则找显著变化
  ↓
选少量代表 Case
  ↓
LLM解释
  ↓
周报
```

这样更便宜、可复现，也能历史对比。
