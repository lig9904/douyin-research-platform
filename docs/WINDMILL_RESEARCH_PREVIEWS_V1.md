# Windmill ASR/L3 零调用预览 V1

日期：2026-09-19

## 结论

新增两个只允许人工触发的准备度预览：

- `f/content_research/flows/manual_asr_preview`
- `f/content_research/flows/manual_l3_preview`

它们复用核心 ASR/L3 执行控制层计算调用上限、成本估算和执行准备状态，但当前不是正式执行器。

两个 Flow 都：

- 默认 `execute=false`
- 不附带 schedule
- 不接受真实视频 ID、媒体地址或证据正文
- 不挂 PostgreSQL Resource
- 不读取 Windmill Variable 或 Secret
- 不构造具体 Provider
- 不写数据库
- 不消耗预算
- 不发起外部请求

## ASR 预览

输入仅包括计划使用的 provider、模型/引擎版本、API/ASR 估算成本、币种和轮询上限。

输出包括：

- 最大外部调用数：1 次提交 + 0–3 次轮询
- 估算总成本；任一成本未知时保持 NULL
- 币种
- `execution_ready`：仅表示成本估算是否完整
- `adapter_status=blocked_unverified_contract`
- `paid_execution_available=false`
- 实际调用数 0、LLM 调用数 0、SDK 重试数 0

## L3 预览

输入仅包括计划使用的 provider、模型版本、Prompt 版本、LLM 估算成本和币种。

输出包括：

- 最大外部调用数 1
- 最大 LLM 调用数 1
- 估算总成本；未知保持 NULL
- 固定 Schema `l3-research-v1.0.0`
- `adapter_status=blocked_unverified_contract`
- `paid_execution_available=false`
- 实际调用数 0、SDK 重试数 0

## execute=true 的行为

当前即使用户填写核心控制层规定的精确确认字符串，Flow 仍会返回“适配器契约未核验，付费执行已禁用”。

这不是故障，而是有意的失败关闭：

1. 当前没有可信的 base URL、鉴权方式、模型能力名和响应 Schema。
2. 无法证明上游不存在隐式重试。
3. 无法可靠映射 token、费用、异步状态或结构化输出。
4. 提前连接正式密钥会把“界面已存在”错误等同于“生产能力已验证”。

因此本阶段只验证 Windmill 参数、默认值、调用上限和成本 NULL/0 语义。

## 后续转正式执行器的条件

必须先取得并核验脱敏的实际契约：

- ASR：提交/轮询地址、鉴权、媒体传递、任务状态、分段、费用和轮询收费规则。
- L3：base URL、鉴权、model、结构化输出、token/费用、超时和隐式重试规则。
- 明确 Secret Variable 路径，但不把 Secret 值写入 Git、Flow 输入、Issue、PR 或聊天。
- 为当天预算单独配置最小请求数与成本上限。
- 用合成或非敏感输入完成预览和失败路径测试。
- 再次取得真实付费调用授权。

满足这些条件后，应将正式执行脚本与 Preview 保持分离，避免把历史预览任务误认为真实研究任务。
