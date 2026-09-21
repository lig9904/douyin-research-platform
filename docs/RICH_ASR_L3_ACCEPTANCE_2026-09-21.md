# 内容充分样本 ASR → L3 → 页面验收

日期：2026-09-21。环境：`test-research` 测试工作区。本文只记录可公开的运行标识、统计和边界；不保存凭据、完整转写正文、原评论或完整模型输出。

## 验收结论

《那一刻，我也被净化了》已在测试服务器完成真实媒体复用、人工音频审核、火山 ASR、人工 L3 正文审核、火山方舟 L3、结果入库、研究台展示及零付费重放。该样本时长约 73.1 秒，转写有 143 个非空白字符、126 个中文字符和 8 个分段，足以验证内容充分的人声链路；它取代了此前“几乎没有人声”及正文贫乏样本作为业务价值案例。

本案例通过的含义是：系统能把真实、经人工审核的音频和正文转换为结构化研究结果，并如实标明缺失证据。它不等于视觉/OCR、原评论语义或平台机制已经采集，也不等于火山供应商实际日账已经接通。

## 输入与人工边界

- 视频 ID：`ed72580d-6ec3-41db-985f-0750e3b62dc7`。
- 音频时长：`73142 ms`；用户已在研究台试听并完成音频审核。
- L3 证据版本：`l3-evidence-v1.0.0`；发送模态为公开视频元数据、评论数字统计和完整转写，不发送原评论文本。
- 用户在页面核对正文后确认交由火山方舟分析；保存的审核版本为 `privacy-v1`，输入指纹为 `77d1ed4a7215ba26787fafa199f11fd135ce3f8eac05e7cc96dc216a80ae4182`。
- 研究材料没有视觉/OCR、原评论文本、对比样本、平台机制证据或目标 IP 约束；页面和结果不得补造这些信息。

## ASR 输出与接口证据

- 接口：Windmill `dispatch_reviewed_asr` → 火山 `volcengine-doubao-asr` → `poll_pending_asr` → 研究数据库。
- 提交父运行：`01a0c3d8-3b8f-2eb9-0294-c0559052e77f`，结果为 examined=2、existing=1、dispatched=1、failed=0、blocked=0。
- 轮询运行：`01a0c3d9-a69d-4c4e-07a7-2c19746db1a0`，结果为 selected=1、completed=1、failed=0、pending=0、limited=0。
- Provider/模型：`volcengine-doubao-asr` / `bigmodel`，模型版本 `2.0`，引擎 `volc.seedasr.auc`。
- 持久化统计：char_count=143、nonspace_count=143、cjk_char_count=126、segment_count=8、quality_status=`usable`。
- ASR 供应商实扣金额仍为 unknown/NULL，页面按“待对账”展示，未伪装成免费或 0 元。

## L3 晋级、输出与费用

该视频最初因生产晋级规则 `below_min_score` 未进入 L3。验收没有直接改数据库或降低全局生产阈值，而是以真实 `L3PromotionGate` 运行一次仅限本批、max_items=1、top_n=1、min_score=0 的验收配额：

- 晋级预览运行：`01a0c3e6-b7fd-eb10-9899-02454657204c`。
- 晋级批次：`080676f9-7e4f-437a-b0c9-89642662db36`。
- 晋级 pipeline run：`40b5f61d-2284-4981-b29f-77f161ab58f2`。
- 选择理由：`top_n_and_daily_quota`，rank=1；晋级步骤 external_calls=0、llm_calls=0。

用户保存与指纹绑定的 L3 审核后，真实执行结果为：

- 分发父运行：`01a0c3e8-8883-9981-c5ee-d91abf5b36f0`，examined=2、existing=1、dispatched=1、failed=0、blocked=0。
- L3 job：`3f42ea22-ef31-4e32-a6fb-ca803318ee92`，completed、attempt_count=1、sdk_retries=0、budget_reconciled=true。
- Provider/模型：`volcengine-ark-chat-completions` / `doubao-seed-2-0-lite`，响应版本 `doubao-seed-2-0-lite-260428`。
- Prompt/Schema：`ark-l3-evidence-boundary-zh-hans-v2-2026-09-21` / `l3-research-v1.0.0`；任务输入指纹与审核指纹完全一致。
- 分析 ID：`d3fa614b-d44f-4b24-9b79-e0e1755b9a01`，completed；视频研究等级更新为 L3。
- 任务成本：LLM `0.005883 CNY`，total `0.005883 CNY`，cost_basis=`estimated`，不是供应商账单实扣。
- 内部当日预算账本：used_requests=3、unknown_price_requests=0、spent_cost=`0.016965 CNY`；次数和金额上限均为 NULL，符合用户取消金额限制的配置。

立即重放运行 `01a0c3ea-c34a-4d56-fac1-32a799470b11` 返回 examined=2、existing=2、dispatched=0，耗时 0.505 秒，未产生第二次模型调用或费用。

## 页面与业务结果

研究台真实页面显示该视频为 L3，并展示媒体、ASR 和七类结构化分析：narrative 6、hooks 3、comment semantics 1、comparisons 1、mechanism hypotheses 1、IP fit 1、limitations 5。

页面对材料边界的处理符合要求：评论语义明确说明没有原评论文本；对比、推荐机制和 IP 适配均说明没有相应证据；limitations 明确列出原评论、对比、机制、目标 IP 和视觉证据缺失。费用区显示本 L3 任务 LLM 估算 `0.005883 CNY`，同时把上游 ASR 费用标为未知，不用 L3 任务成本覆盖 ASR。

非 Secret 变量 `f/content_research/l3_budget_preview_config` 已更新为实际 Provider、模型、版本、Prompt 和价格版本。预览仍显示 `estimate_required`，因为没有虚构通用单任务金额；真实完成任务的估算金额在任务成本区单独展示。

## 自动运行补证

恢复后的六小时计划为 `0 10 */6 * * *`、`Etc/UTC`、enabled。原定 UTC 12:10（北京时间 20:10）的任务 `01a0c310-376c-478a-170d-0d71f402e2c5` 已按时运行成功：pipeline run `cc1b4f96-8219-4d2c-98e6-4bd55699d79b`，observations=2、scored_videos=2、provider_call_count=1、external_calls=1、new_candidate_count=0。因为没有新候选，评论、媒体选择和媒体摄入按门控跳过，没有为既有数据重复调用下游接口。

下一任务 `01a0c3df-81d7-152a-ed12-868b65da8305` 已生成，计划于北京时间 2026-09-22 02:10（UTC 18:10）执行。以上同时证明计划启用、真实到点执行和后续任务生成；一次无新候选周期不证明未来每次都有新内容。

## 完成与剩余

已完成：内容充分的人声 ASR → L3 → 页面案例、新版计划首个真实到点周期、幂等重放、数据库/MinIO/应用回滚的既有恢复验收。

仍待用户完成最终业务使用验收后正式发布 V1。火山侧目前只有任务级报价估算和官方可获得的按天按产品账单口径；实际供应商日账自动同步、ASR 实扣金额和跨日回补仍属于后续增强，不能用本记录的估算金额替代。
