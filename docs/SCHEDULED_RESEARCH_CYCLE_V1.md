# 无人值守研究证据周期

## 输入与输出

固定流程 `f/content_research/flows/scheduled_research_cycle` 无公开参数。读取服务器已有数据库、TikHub Secret、自动身份、评论规则和媒体配置；普通配置变量 `f/content_research/scheduled_golden_settings` 的精确 JSON 字段为 `max_items`（1–5 整数）、`date_window_hours`（1/24/72/168 整数）。新版模板采用 5 条、72 小时窗口的服务器配置，每 6 小时执行一次；实际生效仍以发布后的远端变量、计划和运行证据为准。

流程串行执行：

1. `scheduled_golden_intake` 复用低粉榜→详情→L1。独立固定服务身份，不伪造 `WM_END_USER_EMAIL`，不修改手动入口审核。保留 Provider 缓存，force_refresh=false；每批最多 2 次未缓存调用，零重试，不设置金额或每日调用次数上限。榜单中的既有视频仍保存发现、指标和 L1 证据，但只有本批首次入库的视频才补付费详情；没有新增视频时不调用详情接口。
2. `process_comment_batch` 只接受上一步实际完成的 discovery run_id，并由持久化 `new_candidate` 标志限制为本批新增且已评分的视频；既有视频不再重复进入评论、L2 或媒体链路。人工入口保持原有“处理所选批次全部已评分视频”的语义。
3. `select_cycle_media` 只读选择这一成功评论批次的成功 L2 视频；错误批次拒绝，超过 5 条拒绝，空批次明确返回 0。
4. 串行调用既有 `ingest_video_media`，复用已有私有对象，不请求付费接口。媒体失败中止流程并保留子任务证据。

Windmill 持久化每一步结果和任务状态，因此可追溯源批次、评论批次及媒体结果。流程不使用占用普通 Python Worker 等待子脚本的父 Python 调度器；媒体循环 parallel=false、skip_failures=false，流程 concurrent_limit=1。发现与手动采集共用非阻塞数据库锁；争用、没有新增候选或没有评分视频时结束本周期，不读取评论 Secret、不向下游传无效 ID。

## 边界和恢复

- 本流程不创建隐私审核、不提交 ASR/LLM。已有审核派发和 ASR 轮询保持独立；新音频和新正文必须经过原有有效审核。
- 不新增自动重试。失败时检查失败步骤，使用已保存批次恢复下游，不为恢复评论或媒体重新采集上游。无法确认请求结果时保留失败状态，不盲目重发。
- 下一周期可以发现新批次；Provider 缓存决定榜单请求是否需要外部调用，持久化 novelty 决定是否允许详情、评论和媒体。周期数、Provider 调用数、未缓存外部调用数与新增候选数必须分别记录，不能把缓存命中算付费调用。
- 原始正文、密钥和临时签名 URL 不出现在流程参数或汇总输出。
- 计划模板在 UTC 00:10、06:10、12:10、18:10 执行，默认 disabled。必须现场验证固定配置、一次“全部既有”零下游周期和一次含新增候选的真实链路后再宣称新版生效；不是简单发布 YAML 即完成。
- 尚不解决媒体信息不足、逐次 token 持久化、火山实际日账、恢复/回滚和最终业务验收；不能将本流程当成完整 V1 完成。

## 验收证据要求

每周期保留 Windmill flow job ID、schedule trigger、发现 run_id、评论 run_id、各媒体 asset_id 与复用标记、外部调用和实际供应商日账更新时间。至少两周期证明绑定一致、无重复付费；分别检查失败/空批次/锁争用，不把空队列 ASR 轮询当作研究业务周期。

新版发布验收至少覆盖三种批次：混合新旧时只有新增项进入付费详情和下游；全部既有时只有榜单发现且流程停止；人工黄金入口仍可按明确操作处理既有视频，不被定时 novelty 规则改变。源码、锁和 Flow 合并不等于测试服务器已生效。

## 2026-09-21 首次服务器执行检查点

部署代码 `f07f58377d380b1019e719ae4163822b062ad4f9`（PR #96）。首次 flow push 错将 YAML 文件作为目录，CLI 解析失败；改为 `.flow` 目录后发布成功，原失败日志保留。

- Windmill flow：`01a0c1a5-473b-16c7-5ec6-1fb03494949f`，UTC 01:47:09 创建，数据库状态 `success`，触发类型 `webhook`，不是定时周期。
- 发现批次：`e5b403a6-b191-45da-a10f-6ef2bd0959b9`。配置最多 3 条、24 小时；实际返回并评分 2 条，外部调用 2、缓存命中 0、SDK 重试 0。
- 评论批次：`cf206a7b-c01b-4b0c-aa8a-8d4793c199ee`，成功，明确回显上述发现批次，video_count=2、selected_count=1。
- 媒体选择返回同一评论批次和两个内部视频 ID。媒体运行 `756608f5-d37d-4c58-9ccc-2e3309542617`、`6fc97611-f513-47db-84b8-27f22cf9a2c6` 均 completed、reused=true、external_paid_calls=0。
- 原始运行日志在测试服务器 `/srv/douyin-research-test/evidence/cycle-first-20260921.log`。上述结果经服务器数据库读取核对；尚未据此核算评论请求费用或最新供应商日账。
- 正常小时计划已创建并由 CLI 确认 enabled；沿用模板每小时 UTC 第 10 分钟，无加速测试频率。两个真实 schedule 周期仍待观察，不把启用成功当作连续执行验收。

本检查点不代表 ASR/L3 自动派发、信息充分性、恢复/回滚或 V1 最终验收通过。

## 首个真实定时周期（UTC 02:10）

UTC 02:13:54 读取 Windmill 数据库核验：任务 `01a0c1ac-dce4-e937-9bb8-5685c842fc4e` 的 trigger_kind 为 schedule，完成状态 success。下列子步骤按 parent_job 精确关联，不以时间邻近推断归属。

- 发现 run_id `9fdfb15d-cbe2-40b8-8f46-119df79efc90`：1 条视频、1 条评分，外部调用 2，缓存命中 0，重试 0。
- 评论 run_id `a3352419-f2f2-4366-92e6-58e843027852`：成功，source_run_id 匹配上述发现批次，video_count=1、selected_count=0；没有强迫晋级 L3。
- 媒体选择匹配同一评论批次，仅选中 `4ffa7104-293c-43e0-bf5a-596537cd3cf1`。媒体运行 `187063d7-020b-4bd1-bffd-4a76e585ead5` completed、reused=true、external_paid_calls=0，复用既有两个对象。
- 下一轮任务 `01a0c1ba-30d8-55b3-e539-fa4cb8dc6c13` 已在队列，scheduled_for 为 UTC 03:10、running=false。排队不算成功；仍缺第二个连续周期证据。

本轮不是零外部调用：发现环节确有 2 次未缓存调用。媒体复用不能推导整轮免费；评论调用费用与供应商日账须另核对。

## 第二个真实定时周期（UTC 03:10）

UTC 03:10:51 直接读取服务器 Windmill 数据库：已核验的 schedule 任务
`01a0c1ba-30d8-55b3-e539-fa4cb8dc6c13` 完成状态 success，直属四个步骤全部 success。

- 发现 run_id `086f41b1-1710-415c-88d9-928e8c9af8a9`，completed，external_calls=1。
- 评论 run_id `f6f1bb08-43e1-4c3e-a9f9-ea1ef66adb42`，completed，source_run_id 精确匹配发现批次；video_count=1、selected_count=0。
- 媒体选择回显同一评论批次，仅选择视频 `4ffa7104-293c-43e0-bf5a-596537cd3cf1`。
- 媒体 run_id `df0c96b2-6238-48a6-b00d-0b608580472e`，completed、reused=true、external_paid_calls=0；复用资产 `0e4a6754-0fe8-4032-b96b-4f705088c2b4` 与 `f1be0ba9-8d7f-463c-a81f-8fe9d10824e8`。

至此两个连续小时 schedule 周期均有实际成功及批次绑定证据。只证明发现→评论/L2→媒体的连续执行，不代表完整 ASR/L3 无人值守验收、整轮免费、费用终账或失败恢复验收。

以上两个小时周期属于旧版行为：第二周期对既有视频再次进入评论/媒体复用链路，正是新版 `new_candidate` 持久化门控和六小时节奏要消除的重复工作。保留该记录作为变更前证据，不将其描述为新版已验收。
