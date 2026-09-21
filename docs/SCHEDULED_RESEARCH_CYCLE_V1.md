# 无人值守研究证据周期

## 输入与输出

固定流程 `f/content_research/flows/scheduled_research_cycle` 无公开参数。读取服务器已有数据库、TikHub Secret、自动身份、评论规则和媒体配置；新增普通配置变量 `f/content_research/scheduled_golden_settings`，精确 JSON 字段为 `max_items`（1–5 整数）、`date_window_hours`（1/24/72/168 整数）。验收批次采用 3 条、24 小时窗口。

流程串行执行：

1. `scheduled_golden_intake` 复用低粉榜→详情→L1。独立固定服务身份，不伪造 `WM_END_USER_EMAIL`，不修改手动入口审核。保留 Provider 缓存，force_refresh=false；每批最多 2 次未缓存调用，零重试，不设置金额或每日调用次数上限。
2. `process_comment_batch` 只接受上一步实际完成的 discovery run_id，复用现有评论缓存、L2 与候选持久化。
3. `select_cycle_media` 只读选择这一成功评论批次的成功 L2 视频；错误批次拒绝，超过 5 条拒绝，空批次明确返回 0。
4. 串行调用既有 `ingest_video_media`，复用已有私有对象，不请求付费接口。媒体失败中止流程并保留子任务证据。

Windmill 持久化每一步结果和任务状态，因此可追溯源批次、评论批次及媒体结果。流程不使用占用普通 Python Worker 等待子脚本的父 Python 调度器；媒体循环 parallel=false、skip_failures=false，流程 concurrent_limit=1。发现与手动采集共用非阻塞数据库锁；争用或没有评分视频则结束本周期，不向下游传无效 ID。

## 边界和恢复

- 本流程不创建隐私审核、不提交 ASR/LLM。已有审核派发和 ASR 轮询保持独立；新音频和新正文必须经过原有有效审核。
- 不新增自动重试。失败时检查失败步骤，使用已保存批次恢复下游，不为恢复评论或媒体重新采集上游。无法确认请求结果时保留失败状态，不盲目重发。
- 下一周期可以发现新批次；Provider 缓存决定是否需要外部请求。周期数、Provider 调用数、未缓存外部调用数必须分别记录，不能把缓存命中算付费调用。
- 原始正文、密钥和临时签名 URL 不出现在流程参数或汇总输出。
- 计划模板每小时 UTC 第 10 分钟执行，默认 disabled。必须现场验证固定配置、单次真实链路、两个连续 schedule 周期后再宣称上线；不是简单发布 YAML 即完成。
- 尚不解决媒体信息不足、逐次 token 持久化、火山实际日账、恢复/回滚和最终业务验收；不能将本流程当成完整 V1 完成。

## 验收证据要求

每周期保留 Windmill flow job ID、schedule trigger、发现 run_id、评论 run_id、各媒体 asset_id 与复用标记、外部调用和实际供应商日账更新时间。至少两周期证明绑定一致、无重复付费；分别检查失败/空批次/锁争用，不把空队列 ASR 轮询当作研究业务周期。

## 2026-09-21 首次服务器执行检查点

部署代码 `f07f58377d380b1019e719ae4163822b062ad4f9`（PR #96）。首次 flow push 错将 YAML 文件作为目录，CLI 解析失败；改为 `.flow` 目录后发布成功，原失败日志保留。

- Windmill flow：`01a0c1a5-473b-16c7-5ec6-1fb03494949f`，UTC 01:47:09 创建，数据库状态 `success`，触发类型 `webhook`，不是定时周期。
- 发现批次：`e5b403a6-b191-45da-a10f-6ef2bd0959b9`。配置最多 3 条、24 小时；实际返回并评分 2 条，外部调用 2、缓存命中 0、SDK 重试 0。
- 评论批次：`cf206a7b-c01b-4b0c-aa8a-8d4793c199ee`，成功，明确回显上述发现批次，video_count=2、selected_count=1。
- 媒体选择返回同一评论批次和两个内部视频 ID。媒体运行 `756608f5-d37d-4c58-9ccc-2e3309542617`、`6fc97611-f513-47db-84b8-27f22cf9a2c6` 均 completed、reused=true、external_paid_calls=0。
- 原始运行日志在测试服务器 `/srv/douyin-research-test/evidence/cycle-first-20260921.log`。上述结果经服务器数据库读取核对；尚未据此核算评论请求费用或最新供应商日账。
- 正常小时计划已创建并由 CLI 确认 enabled；沿用模板每小时 UTC 第 10 分钟，无加速测试频率。两个真实 schedule 周期仍待观察，不把启用成功当作连续执行验收。

本检查点不代表 ASR/L3 自动派发、信息充分性、恢复/回滚或 V1 最终验收通过。
