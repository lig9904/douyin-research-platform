# OpenAI Responses L3 Adapter V1

日期：2026-09-19

## 范围

`OpenAIResponsesL3Provider` 定义 OpenAI Responses API 与现有
`L3ProviderRequest` / `L3ProviderResponse` 的离线编解码边界。当前版本不持有
HTTP 客户端、不接收 Secret，也没有任何可发送网络请求的生产或测试旁路。它不改变
L2→L3 晋级、每日 Top-N、预算预占、单并发锁、幂等和结果持久化规则。

## 固定行为

- 契约目标是 HTTPS `POST /v1/responses` 与 Bearer header 鉴权；当前实现不执行该请求，也不接收 Secret。
- 请求使用不可变的模型 revision，而不是浮动 alias。
- `store=false`，不请求供应商保存响应。
- 使用 strict JSON Schema，字段与 `l3-research-v1.0.0` 一一对应，并要求响应
  回显 task key、Prompt/Schema 版本和输入指纹。
- 一次调用，客户端和协调器重试均为 0；不处理流式响应或后台任务。
- 必须返回 `completed`、完全一致的模型 revision、唯一 `output_text` 和非负
  token usage，否则 fail closed。
- token 数乘以部署时显式配置的每百万 token 单价；价格配置必须带版本，成本
  记录为 `estimated`，不把估算冒充供应商账单。
- 离线状态码校验不接收响应体，错误信息不会包含上游诊断内容。

## 隐私边界

Adapter 只接收协调器组装的 evidence bundle，不自行查询 TikHub、数据库或其他
外部来源。调用方必须在组装 evidence 前完成数据最小化和隐私审查，并提供
`privacy_review.reviewed=true` 与非空审查版本。隐私状态来自调用前已验证的输入，
不允许模型自行声明或授予；原始响应不会持久化。

## Production readiness

固定并继续保持 `production_ready=false`，构造函数不提供开启参数，类中也没有
HTTP 传输方法。生产启用必须通过后续独立代码审查新增传输并修改固定契约，不能由
运行时输入、私有方法或替换测试客户端切换。在以下项目由人工针对目标账号
和部署环境完成核验之前，执行会在发出 HTTP 请求前失败：

1. 目标模型 revision 在账号内可用且不会被浮动 alias 替换；
2. Structured Outputs schema 与目标模型兼容；
3. 账号的数据保留和训练设置满足隐私要求；
4. 输入/输出 token 单价、币种和账单舍入方式完成对账；
5. 网关、代理和客户端均没有隐式重试；
6. 内容安全、超时和不确定执行的人工 reconciliation 流程已经演练。

本阶段不读取正式密钥、不执行真实或付费调用。`httpx.MockTransport` 仅在测试代码中
承载合成请求，用于验证序列化、状态码、响应映射和成本计算；生产模块不会调用它。
协议依据为 OpenAI 官方
[Responses API reference](https://platform.openai.com/docs/api-reference/responses/create)
与 [Structured Outputs guide](https://platform.openai.com/docs/guides/structured-outputs)。

本仓库复核使用的无密钥契约摘要位于
`docs/provider_contracts/openai_responses_l3_v1.json`。指纹按文件原始字节计算：

```bash
sha256sum docs/provider_contracts/openai_responses_l3_v1.json
```
