# 固定配置 L3 Worker

状态：入口与隔离测试已实现；未部署、未启用自动计划、未调用真实模型。

入口 `f/content_research/analysis/run_reviewed_l3` 只接受 `approval_id`（研究台审核返回的 `human_annotation.id`）。读取最新有效隐私审核中的视频、证据版本与指纹，再通过已有 `execute_reviewed_live_ark` 重组并核验当前证据。既有 L2→L3 晋级和研究级别检查仍由 assembler 执行；入口不创建审批、转写或候选。

## 固定服务端配置

- 数据库：`f/content_research/research_db`。
- 自动身份：既有 `f/content_research/automation_worker_identity`；不依赖人工网页登录。
- 新增受控 Secret：`f/content_research/l3_worker_config`。部署前由现有已核验配置填写，不能复用 `l3_budget_preview_config` 的未配置占位值。

JSON 顶层必须恰为 `ark`、`prompt_version`、`max_daily_requests`、`max_daily_cost_cny`。后两个字段明确设为 null 表示不限对应额度，不代表调用免费；不自动设置默认金额上限。

`ark` 对应 `LiveArkConfiguration`：必须提供真实 `api_key`、`endpoint_id`、`model_revision`、`expected_response_model`、`pricing_version`、`input_cost_per_million_tokens`、`output_cost_per_million_tokens`。可选 `model_id` 沿用推荐的 `doubao-seed-2-0-lite`，`cost_currency` 必须 CNY，`timeout_seconds` 沿用服务默认值。价格必须有对应版本来源，不猜测、不填零占位；不得使用 qwen-flash。凭据不进入 Git 或任务参数。

## 执行与恢复

- 旧审批 ID 若已被后续审核替代，会在账本初始化前拒绝。执行前 assembler 和 coordinator 仍重新检查当前审核、证据和候选资格。
- 稳定任务键绑定视频、证据、模型版本、Prompt 和 Schema；没有调用方任意任务键。
- 新任务只在当天账本不存在时按固定策略初始化；已有账本不覆盖。旧任务沿用原账期，原账本缺失即要求核对，不补零继续。
- 预估费用未知保持 None；结果 token 费用由现有 Provider 的版本价格计算，不冒充供应商实扣日账。
- 输入、输出 token 单价必须严格为正；零价格占位在读取审核及初始化账本前拒绝。执行服务再次校验，并在成功或异常路径关闭自己构造的 Provider。
- 当前入口不计算付费前的保守费用上界，因此显式设置非空金额上限会由协调器拒绝未知预估，不会绕过限制；本项目已授权不限金额的配置使用 null。请求数量限制仍可独立使用。
- 只有 `completed` 返回成功；其他状态使 Windmill 任务失败并给固定脱敏错误。恢复同一审批及配置不重复模型提交；运行中不确定状态保留人工核对要求。

## 验证与剩余工作

定向测试覆盖固定参数、配置校验、失败脱敏、最新审批读取、撤销/替代拒绝、新账本初始化不覆盖、旧账期与缺失账本拒绝；数据库测试仅使用隔离 PostgreSQL，审批为测试夹具。协调器现有回归覆盖持久化、模型响应和结算恢复。

仍需独立审查、Windmill 实际依赖安装、配置落地、自动调度、真实模型结果和页面展示。此脚本不等于全链路调度已经接通，也不允许通过手工写批准行完成服务器验收。
