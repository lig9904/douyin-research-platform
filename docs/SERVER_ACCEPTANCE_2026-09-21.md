# 测试服务器验收检查点

日期：2026-09-21。范围：现有浏览器 JumpServer 会话中的服务器操作与数据库只读核验。不是最终 V1 验收。

## 最新现场检查点（2026-09-21，北京时间 08:17 后）

本节日志均位于服务器固定目录 `/srv/douyin-research-test/evidence/`，仅引用文件名，凭据和原始正文不进入 GitHub。

- PR #92 已合并，服务器代码与研究台发布至 `f66ea300b8d4a8820714fe30f6b5052c988bdafc`；页面实际显示转写和 L3 结果。三项 CI 通过；转写面板经 React 渲染测试、独立复查和服务器截图核验。
- 用户亲自保存《帮室友带道》音频审核后，真实 ASR 提交 1 次、查询共 2 次，任务 completed；转写 33 字符、音频 28,723 ms，机器状态 usable。费用仍为 unknown / NULL，不当作免费，也不代表人工准确率验收。重复入口返回 external_calls=0。证据日志：`asr-reviewed-first-20260921.log`、`asr-poll-first-20260921.log`、`asr-replay-20260921.log`。
- `poll_pending_asr` 每五分钟计划已启用；UTC 23:55 和次日 00:00 两个真实 schedule 周期均 success，selected=0。它们只证明空队列轮询，不代表两个完整业务闭环。
- Ark 固定合成探测仅调用一次，HTTP 200、结构化校验通过，实际响应 `doubao-seed-2-0-lite-260428`；输入 221、输出 106 tokens。日志 `ark-discovery-20260921.log`。控制台核对该版本和小输入档价格：输入不超过 32k 时文本输入 0.6、输出 3.6 CNY/百万 tokens；这是非缓存估算价格，不是实际账单。L3 Secret 已绑定该响应版本，金额与每日次数上限均 null；尚未启用 L3 定时计划。
- 用户在聊天确认精确正文可发送至火山后，通过真实登录页面保存指纹 `d3a276c06393f0b1a2628591b36bbdf912738b3db2deacc6bacab28faf78f5c6` 的审核。一次真实 L3 completed、attempt_count=1、external_calls=1、sdk_retries=0；数据库独立联查确认输入指纹一致，分析 ID `36e7126f-5c52-4e8f-99b2-b149026c99ab`，估算 LLM 费用 0.006086 CNY。重复执行 external_calls=0。日志 `l3-reviewed-first-20260921.log`、`l3-replay-20260921.log`。不在 GitHub 保存正文、姓名、凭据或完整模型输出。
- **业务质量仍未通过**：实际查看输出发现英文、从评论计数推断用户意图、无比较证据却生成比较、推断推荐机制等问题。仅证明审核→调用→结果/费用入库→页面展示及幂等链路，不能当作可用研究结论。下一步版本化中文与证据约束提示并复验，保留首条失败质量样本。
- 火山实际日账尚未接入；L3 当前持久化估算金额，但尚未保存逐次 token 明细。恢复演练、回滚、自动发现到分析的连续业务周期及最终用户验收仍未完成；不发布正式 V1。

## 历史现场检查点（UTC 2026-09-20 23:45）

- 服务器代码已更新至 `691acf5cbe9e7f9a25331981ee34b88973195604`（PR #90）；研究台和黄金采集脚本已分别发布，退出码均为 0。黄金入口依赖固定至代码提交 `90f613363b70517765091a3adb8b85a568cab0e1`，不以服务器 HEAD 代替脚本依赖版本。
- PR #90 修复单批最多两次外部调用误作每日限额的问题。每日累计不清零，金额和每日次数不设上限；每批仍有两次未缓存调用边界，缓存不占调用额度。真实 PostgreSQL 定向回归 42 passed，四项 CI 通过，独立复核后合并。
- 已通过登录研究台刷新一批真实来源：3 条观察、2 次未缓存请求、0 次重试。随后三条视频均完成实际下载、音频提取和私有 S3 入库。

| 平台视频 ID | WAV 字节 | MP4 字节 | 媒体 pipeline_run |
| --- | ---: | ---: | --- |
| 7686842340602349555 | 1431960 | 3835652 | d1c3947c-ec1f-44f8-8e92-33e560e26576 |
| 7686820996988799593 | 919264 | 1938979 | 57332a99-7288-469d-8583-164367526b82 |
| 7686838579566761593 | 337464 | 2327157 | f0ff322d-6df7-4f22-827a-b6f57b6fa580 |

数据库独立联查确认 3 条视频对应 6 个媒体记录，均有 64 位摘要。首条重跑 `4e56b794-5ef2-4525-88df-4d12c2ef4085` 返回 reused=true，复用同一 video/audio asset_id；四次媒体执行均 external_paid_calls=0。全部结束后临时 `research-media-*` 目录数量为 0。日志位于服务器 evidence 目录的 `media-refreshed-run-20260921.log`、`media-replay-20260921.log`、`media-second-video-20260921.log`、`media-third-video-20260921.log`。

私有存储另用 47 字节对象验证内网写读、公网 HTTPS 签名读内容一致，匿名同路径 HTTP 403。研究台首条 WAV 试听已实际加载并出现播放中的“暂停”状态；5 分钟链接过期后重新生成并恢复播放。尚未由用户保存音频审核，不虚构审核记录，不将音频播放等同 ASR 完成。

用户试听首条后反馈“几乎没有人声”；它可以证明媒体处理，不适合作为清晰语音转写的唯一验收样本。已切换《帮室友带道》的现有 WAV 试听，未重新采集；后续选择清晰口播样本验证转写，不为无人声内容伪造文本。

最新 TikHub 日账：账期 2026-09-20（America/Los_Angeles），USD 0.106000，18 次请求、8 次付费，fetched_at=2026-09-20T23:38:16.396167+00:00。供应商同步结果、数据库和研究台首页均核对一致；这是当前日累计，不是终账，也不含未接入的火山实际账单。

固定配置现状：`media_storage_config`、`asr_worker_config`、`ark_api_key` 均已保存为 Secret；自动身份、评论批次规则和无金额上限的每日策略已设置。未记录密钥值。`l3_worker_config` 尚未完成模型响应标识及价格绑定，ASR/L3 尚无真实成功结果。只有费用小时计划启用，不能把它当作业务链路连续调度。

新备份 `/srv/douyin-research-test/backups/20260920T234425Z` 创建退出码 0，并于 UTC 23:45:01 独立校验返回 BACKUP_VALID。它包含研究库、Windmill 库及全局角色归档，不包含 MinIO 对象正文、宿主机环境文件或容器镜像。尚未做恢复演练、回滚和最终用户验收。

历史失败保留：旧缓存四个播放 URL 均 HTTP 403；首次媒体任务 `b2eac4b2-80c2-4ad6-9cd6-38cb48cf81e6` 为 media_download_failed，付费调用 0。未绕过签名或 CDN 拒绝；刷新来源后正常下载。CLI 黄金入口缺少终端用户身份被拒后改用真实登录页面，没有伪造身份。

## 早期检查点（保留历史，最新状态以上节为准）

### 已部署

- 代码：`9d142d735b6be67b6ea24800770bc148498961c2`（PR #86）。
- 数据库迁移及账本：至 017，发布工具验证通过。
- 两个普通 Worker：`douyin-research-media-worker:87eeac7`，镜像 ID `sha256:08e0267834a4f76ef1eb33f0fe6a790fe5eddd7dff926e5b0f0712974d549da5`。
- 运行时：Python 3.13.5、ffmpeg、ffprobe；合成一秒音频实际输出 PCM s16le / 16000 Hz / 单声道。
- 临时目录：宿主机 `/srv/douyin-research-test/media-tmp` 绑定容器 `/srv/research-media-tmp`，可写且位于 `/srv` 的 `/dev/sdb1`。未部署应用服务器 Nginx。
- 媒体脚本带完整锁发布；日志 `/srv/douyin-research-test/evidence/media-script-9d142d7.log`。

## 分析脚本发布

在 `test-research` 依次发布以下 `f/content_research/analysis/` 脚本：

1. `run_reviewed_asr`
2. `run_reviewed_l3`
3. `poll_pending_asr`
4. `dispatch_reviewed_asr`
5. `dispatch_reviewed_l3`

来源为容器发布目录 `/tmp/v1-9d142d7`，发布退出码 0；日志 `/srv/douyin-research-test/evidence/analysis-scripts-9d142d7.log`。
独立查询 Windmill `script` 表确认五个非归档脚本存在、锁非空、`lock_error_logs` 为空，创建时间 UTC 2026-09-20 19:21:30–32。
发布前运行中作业数为 0；发布后该工作区 `schedule` 表仍只有费用同步计划。未创建或启用分析计划，未执行上述脚本，未触发付费请求。
这只证明发布与锁元数据，不证明运行时依赖安装或真实业务执行成功。

## TikHub 连续费用调度

计划 `f/content_research/collectors/sync_daily_spend`：enabled=true，cron `0 0 * * * *`，时区 `Etc/UTC`。
联查 `v2_job` 与 `v2_job_completed`，以下作业均为 schedule 触发且 success：

| 作业 ID | 完成时间 UTC |
| --- | --- |
| 01a0bf9f-c47e-5a9c-9bb2-cf66107e3817 | 2026-09-20 17:00:01.233481 |
| 01a0bfc2-ab5b-486a-d8ae-3d65305c9373 | 2026-09-20 18:00:01.497915 |
| 01a0bff9-9aea-b6b4-6af7-eba770ad0e70 | 2026-09-20 19:00:01.165542 |

最新 `supplier_daily_spend`：TikHub，账期 2026-09-20，America/Los_Angeles，USD 0.052000，7 次请求、3 次付费，fetched_at=2026-09-20 19:00:01.092636+00。
因此连续周期和最新落库已验证；未在本次重新核对供应商后台或研究台页面，不代表模型费用已接入。

## 备份与剩余缺口

- 备份 `/srv/douyin-research-test/backups/20260920T190326Z` 通过独立完整性校验；迁移前另有 `20260920T190539Z`。尚未证明可恢复，不能将校验通过等同恢复演练。
- 媒体、自动身份、ASR、L3 四项固定配置最后检查尚不存在；既有凭据文件上传被浏览器权限阻断，等待用户开启文件访问权限，不绕过限制。
- 审核页面、真实媒体、ASR、L3、三条业务样本、业务连续调度、受控失败恢复、隔离恢复、回滚及用户验收仍待完成。
- 下一步补齐配置与页面发布，单次真实样本通过后才启用分析计划。正式 V1 尚未验收或发布。

## 恢复演练审查边界

只读复查 `scripts/test-server-release.sh`：`restore-drill` 仅新建并清理 `test_server_research_restore`、`test_server_windmill_restore`；任一已存在则拒绝复用，不覆盖现用库。现用库仅用于计数对照，`globals.sql` 不在共享集群执行。
当前演练只验证部分关键表计数、对象和 owner，不覆盖媒体、ASR/L3、费用账本、ACL、应用读路径或真实全局角色恢复。运行前应验证归档、磁盘和临时库不存在，并安排稳定写入窗口；旧备份与当前源库计数可能漂移。必须补充业务级验证才能完成 T6，不能只凭脚本退出成功宣布完整恢复。
