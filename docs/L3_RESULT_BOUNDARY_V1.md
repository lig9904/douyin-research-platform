# L3 结构化精研结果边界 V1

日期：2026-09-19

## 结论

本层只接收已经生成且完成隐私复核的 L3 结构化结果，不调用外部 API、ASR 或 LLM。它把 L2→L3 选择、版本信息、输入/输出指纹、结构化结果和逐任务成本连成可审计记录。

Schema 版本：`l3-research-v1.0.0`

## 准入条件

写入前必须满足：

1. 视频仍存在且 `research_level >= 2`。
2. 视频曾由确定性 L2→L3 Gate 记录为 `selected`。
3. model ID、model revision、Prompt version、Schema version 和输入指纹完整。
4. 七个结果分区都有内容：叙事结构、Hook 功能、评论语义、多案例比较、机制假设、IP 适配、局限。
5. 至少声明一种证据模态。
6. `privacy_reviewed=true`。

任一条件不满足时，不写 `analysis_run` 或 `research_task_cost`，也不提升研究层级。

## 结果语义

结果是模型辅助研究结论，不是事实数据库：

- `mechanism_hypotheses` 始终标记为推断。
- `limitations` 强制非空。
- 输入只保存指纹、证据模态和 `raw_evidence_stored=false`，不复制评论、转写或媒体原文。
- 只有成功写入完整结果后，视频才从 L2 提升到 L3。
- Gate 的 `selected` 本身仍不代表 L3 已完成。

## 版本与幂等

`task_key` 是稳定的任务幂等键。相同 task key、输入、输出、版本和成本重放时复用已有记录；任一内容变化都会拒绝覆盖。

数据库同时保存：

- model ID / revision
- Prompt version
- Schema version
- input / output fingerprint
- L3 结构化 JSON
- 与 `research_task_cost` 的唯一关联

相同视频、模型版本、Prompt、Schema 和输入指纹不能写入第二份成功结果。

## 成本

API、ASR、LLM 三项成本分别记录：

- 未知保持 NULL。
- 明确免费才写 0。
- 任一分项未知时，总成本保持 NULL。
- `actual` 或 `estimated` 基础要求三项都有值。
- `unknown` 基础要求至少一项未知。

本层没有任何模型客户端，因此不会自行产生付费调用。
