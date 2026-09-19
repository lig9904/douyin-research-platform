# Windmill L3 人工审核与预算预览 V1

日期：2026-09-20

## 结论

研究台视频详情页增加三段式人工流程：

1. 生成当前 L3 证据候选的脱敏 manifest。
2. 审核人在受控渠道核对正文后，批准该精确指纹。
3. 使用服务端固定的模型和定价配置读取当天预算快照。

这三步不构造 Provider、不读取模型 Secret、不创建 L3 执行任务、不预占预算，也不发起外部或 LLM 调用。页面没有 `execute`、付费确认串或 `task_key` 输入，固定返回 `paid_execution_available=false`。

## 身份与权限

后端只接受 Windmill App 运行时提供的 `WM_END_USER_EMAIL`，不接受页面传入的 actor，也不回退到 publisher 的 `WM_EMAIL` 或 `WM_USERNAME`。身份必须精确命中静态变量：

- `$var:f/content_research/l3_privacy_reviewers`

变量值可以是小写邮箱 JSON 数组，或逗号/换行分隔的小写邮箱。缺失、空值、大小写不规范或非成员均失败关闭。

部署时还应把三个后端 runnable 的 Folder ACL 限制为 reviewer/admin；allowlist 是应用内第二道边界，不替代 Windmill 权限配置。

## 候选与审批

候选在 PostgreSQL `REPEATABLE READ READ ONLY` 事务内生成。Windmill Job 输出仅包含：

- 审核对象 UUID（只在 App manifest 区展示，便于启动受控正文页）
- evidence SHA-256 指纹
- evidence/review 版本
- modality 名称
- 零调用、零写入和无原始证据标记

标题、描述、评论正文、转写正文、URL、数据库资源和 Secret 都不会进入输出。正文复核必须在单独的受控渠道完成；当前页面不会假装已经提供正文审核能力。

批准时，服务端在同一个 `REPEATABLE READ` 事务内重建候选并精确比对指纹、版本和 modalities。客户端只生成幂等键；重复同一请求不重复写，幂等键复用到不同输入会失败，候选变化会在写入前失败。

## 预算预览

预算预览从静态变量读取服务端计划，不接受页面提交的 Provider、模型、价格或币种：

- `$var:f/content_research/l3_budget_preview_config`

示例（不含 Secret）：

```json
{
  "provider": "verified-provider-name",
  "model_id": "verified-model-id",
  "model_revision": "verified-revision",
  "prompt_version": "l3-prompt-v1",
  "pricing_version": "pricing-2026-09-20",
  "estimated_llm_cost": 0.25,
  "cost_currency": "CNY"
}
```

预览只读 `daily_budget` 的当天记录，保留 `NULL`（无限或未知）与精确 `0` 的区别。结果是带时间日期的配置快照，不是供应商报价、账单、额度承诺或预算预占。配置中的负值、非有限值和超过六位小数的金额会被拒绝。

## 上线前验收

- 在隔离 Windmill 环境验证 reviewer/admin 与普通 Viewer 的真实 Folder ACL。
- 用唯一敏感哨兵核对 Job 输入、输出、日志和数据库元数据均不含正文。
- 验证 `WM_END_USER_EMAIL` 缺失和非 allowlist 成员均不能写审批。
- 验证候选内容变化、跨视频指纹、幂等冲突和重复点击均不会产生错误审批。
- 验证预算缺失、币种不匹配、NULL、0、超限和配置非法状态。
- 三个 Python backend runnable 已把私有 `douyin-research-platform` 依赖固定到核心提交 `1558677c40cc22239e660e269738619dfd05388d`。requirements 使用 Windmill 可解析的 PEP 508 写法，并已由本机 CE v1.815.0 生成/运行 lock；正式 sync 前仍须复核提交可达和 lock 变更。
- 继续保持正式付费执行器独立；本工作流不能通过参数升级为执行器。

当前版本已在本机 Windmill CE v1.815.0 跑通合成候选的 prepare→一次性正文页→approve→budget preview：只写入一条指纹绑定的审核记录，预算返回 `budget_missing`，全链路无 Provider/LLM 外呼。它仍不代表测试服务器多用户 Folder ACL、运行时日志保留或生产数据库角色已经完成现场验收。
