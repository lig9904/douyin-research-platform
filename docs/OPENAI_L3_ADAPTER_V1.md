# OpenAI Responses L3 Adapter V1

日期：2026-09-19

## 范围

`OpenAIResponsesL3Provider` 把一次 OpenAI Responses API 同步请求映射到现有
`L3ProviderRequest` / `L3ProviderResponse` 边界。它不改变 L2→L3 晋级、每日
Top-N、预算预占、单并发锁、幂等和结果持久化规则。

## 固定行为

- HTTPS `POST /v1/responses`，Bearer header 鉴权；Secret 只通过构造参数注入。
- 请求使用不可变的模型 revision，而不是浮动 alias。
- `store=false`，不请求供应商保存响应。
- 使用 strict JSON Schema，字段与 `l3-research-v1.0.0` 一一对应，并要求响应
  回显 task key、Prompt/Schema 版本和输入指纹。
- 一次调用，客户端和协调器重试均为 0；不处理流式响应或后台任务。
- 必须返回 `completed`、完全一致的模型 revision、唯一 `output_text` 和非负
  token usage，否则 fail closed。
- token 数乘以部署时显式配置的每百万 token 单价；价格配置必须带版本，成本
  记录为 `estimated`，不把估算冒充供应商账单。
- HTTP 错误不转发响应体，避免上游诊断信息或 Secret 进入日志。

## 隐私边界

Adapter 只接收协调器组装的 evidence bundle，不自行查询 TikHub、数据库或其他
外部来源。调用方必须在组装 evidence 前完成数据最小化和隐私审查，并提供
`privacy_review.reviewed=true` 与非空审查版本。隐私状态来自调用前已验证的输入，
不允许模型自行声明或授予；原始响应不会持久化。

## Production readiness

固定并继续保持 `production_ready=false`，构造函数不提供开启参数。生产启用必须
通过后续独立代码审查修改，不能由运行时输入切换。在以下项目由人工针对目标账号
和部署环境完成核验之前，执行会在发出 HTTP 请求前失败：

1. 目标模型 revision 在账号内可用且不会被浮动 alias 替换；
2. Structured Outputs schema 与目标模型兼容；
3. 账号的数据保留和训练设置满足隐私要求；
4. 输入/输出 token 单价、币种和账单舍入方式完成对账；
5. 网关、代理和客户端均没有隐式重试；
6. 内容安全、超时和不确定执行的人工 reconciliation 流程已经演练。

本阶段不读取正式密钥、不执行真实或付费调用。测试只使用
`httpx.MockTransport`。协议依据为 OpenAI 官方
[Responses API reference](https://platform.openai.com/docs/api-reference/responses/create)
与 [Structured Outputs guide](https://platform.openai.com/docs/guides/structured-outputs)。

本仓库复核使用的无密钥契约摘要位于
`docs/provider_contracts/openai_responses_l3_v1.json`。指纹按文件原始字节计算：

```bash
sha256sum docs/provider_contracts/openai_responses_l3_v1.json
```
