# L2 → L3 Top-N 晋级闸门 V1

日期：2026-09-19  
规则版本：`l2-l3-promotion-v1.0.0`

## 目的

在 ASR、LLM 和其他高成本精研之前，先生成一个可审计、可重放、按日封顶的选择计划。V1 只选择候选，不执行外部调用，也不代表视频已经完成 L3。

## 准入与排序

候选来自一个已成功完成且明确声明平台的 `pipeline_run`。

- `source_video.research_level >= 2`
- 必须存在最新的 `priority` 分数
- 分数不得低于调用时给定的 `min_score`
- 按最新分数降序排列
- 同分时按 canonical video UUID 排序，保证结果稳定
- 缺失分数保持缺失并以 `priority_score_required` 跳过，不补成 0

## 双重上限

选择同时受到两层限制：

1. 单次 `top_n`，代码硬上限为 20。
2. `daily_research_quota` 中预先配置的平台日配额。

闸门不会自动创建或扩大日配额。配额不存在时失败关闭；当日余额不足时，只选择剩余额度允许的数量。不同任务在同一平台、同一天共享配额。

## 幂等与并发

日配额行在事务中使用 `FOR UPDATE` 锁定。候选的 L2 状态与最新分数形成证据指纹：

- 相同来源运行、参数和候选证据重跑：复用原批次，不再次扣配额。
- 候选证据变化：形成新批次。
- 同一视频当天已经选入 L3：后续新批次以 `already_selected_today` 跳过。

数据库的部分唯一索引同时防止并发场景下重复选入。

## 审计记录

每次新选择会写入：

- `pipeline_run`：运行版本、来源、输入数、选择数和零外部调用声明
- `research_promotion_batch`：规则、日期、Top-N、最低分、候选指纹
- `research_promotion_decision`：每个候选的排名、分数、选择结果和原因
- `pipeline_run_item`：`L3_GATE` 阶段结果

跳过原因包括：

- `l2_required`
- `priority_score_required`
- `below_min_score`
- `top_n_limit`
- `daily_quota_exhausted`
- `already_selected_today`

## 语义边界

`selected` 只表示允许进入后续昂贵任务队列。闸门不会：

- 把 `source_video.research_level` 改为 3
- 调用 TikHub、ASR、LLM 或其他外部服务
- 推断叙事结构、评论语义、受众画像或传播机制
- 生成研究结论或内容建议

只有后续 L3 分析成功并保存版本化证据后，才能标记为已完成 L3。
