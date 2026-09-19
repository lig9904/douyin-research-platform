# ASR 证据与单任务成本账本 V1

日期：2026-09-19  
证据版本：`asr-evidence-v1.0.0`

## 目标

为已经完成的 ASR 结果提供版本化、幂等、可审计的离线写入入口，并按任务记录：

- `api_cost`
- `asr_cost`
- `llm_cost`
- `total_cost`

本模块不包含 ASR 客户端，不访问 TikHub、ASR 或 LLM 服务，也不读取正式密钥。

## 准入条件

ASR 是有误差的测量步骤，只允许在视频已经达到 L2 后写入：

- `source_video.research_level >= 2`
- ASR provider 必填
- model ID、model revision、engine version 必填
- 输入媒体的 `source_fingerprint` 必填
- 每个任务必须有稳定、非空的 `task_key`

写入成功不会把视频提升到 L3。ASR 文本只是后续分析的证据，不是语义结论。

## 证据版本与质量

`transcript` 保存：

- 原始转写文本
- 分段起止时间、文本和可选置信度
- 语言、热词版本、来源 provider
- 音频时长
- 质量状态
- 模型及引擎版本

允许的质量状态：

- `unreviewed`
- `usable`
- `low_confidence`
- `no_speech`
- `rejected`

除 `no_speech` 外，全文不得为空。分段必须按时间排序、互不重叠，且不能超出已知音频时长。置信度如存在，范围必须为 0 到 1。

ASR 输出可以存在识别错误；下游不得把转写文本直接视作无误差事实。

## NULL 与 0

`research_task_cost` 的三个成本分量均可为 NULL：

- 未拿到或无法确认成本：保存 `NULL`
- 明确免费、缓存命中或未使用：显式保存 `0`
- 只有三个成本分量全部已知时，`total_cost` 才由数据库计算
- 任一分量为 NULL，`total_cost` 保持 NULL，不用已知分量伪造“总成本”

本 ASR 写入步骤不使用 LLM，因此要求调用方明确传入 `llm_cost=0`。API 成本和 ASR 成本仍可为 NULL。

`pipeline_run` 的历史默认 0 汇总字段不作为未知单任务成本的真值；`research_task_cost` 是本步骤的单任务权威账本。

## 幂等与防重复

任务账本记录输入指纹和输出指纹：

- 相同 `task_key`、证据和成本重放：返回原记录
- 相同 `task_key` 但输入、输出或成本变化：拒绝覆盖
- 同一视频、同一模型版本、同一输入证据使用不同 `task_key` 重复登记：拒绝
- 任务成本与 transcript 采用同一数据库事务写入，不会出现只有成本或只有证据的半成品

输出指纹会覆盖全文和分段内容，但账本只保存哈希；实际 ASR 文本仍按研究证据保存在 `transcript` 表中。

## 边界

该入口不会：

- 发起或重试任何外部调用
- 自动估算未知成本
- 调用 LLM 清洗或解释转写
- 生成叙事结构、Hook、受众或机制结论
- 将 `research_level` 改为 3

后续真实 ASR 执行器必须另外经过预算、并发、确认和重试边界审查，并将结果交给本入口持久化。
