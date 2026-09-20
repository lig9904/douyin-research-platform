# 审核后首次派发与双 Worker 调度

## 业务契约

- ASR 输入：视频绑定的 WAV 资产、最新有效审核、固定私有媒体配置。输出：已有 ASR Worker 的提交状态；后续恢复由 pending poller 负责。
- L3 输入：已晋级候选、最新可信隐私审核、与审核一致的当前证据、固定模型和 Prompt 配置。输出：已有 L3 Worker 的持久化结果。
- 调度入口均无参数：`dispatch_reviewed_asr`、`dispatch_reviewed_l3`。内部仅传资产/审核记录 ID，不接受调用方提供 URL、身份、模型或费用设置。
- 首次派发除精确任务键外，还检查视频和内容指纹的历史执行及费用记录。改模型或 Prompt 不自动重购同一输入。异常记录需核对，不能自动改键重试。
- 每轮最多尝试五个子任务；这是运行时批次，不是金额上限。过期审核不消耗派发名额；读取完整候选流，避免固定前缀挡住新任务。
- 返回计数，不输出原始证据、凭据、签名 URL 或供应商任务引用。未知费用仍为未知。

## 双 Worker 协调

三个父入口（首次 ASR、首次 L3、待处理 ASR 轮询）必须一起部署本版本。
它们共享研究数据库 PostgreSQL 会话级非阻塞 advisory lock `724901830215`。
取得锁的父任务才可同步等待子 Worker；未取得锁立即返回
`status=deferred, reason=analysis_scheduler_busy`，不占 Worker 等锁、不派发任务。
父任务异常退出或连接断开时 PostgreSQL 自动释放会话锁。

至少需要两个可运行 Python 的普通 Worker。子 Worker 不取得该父调度锁，沿用自己的付费执行锁。
不依赖 Windmill Enterprise 的共享 concurrency key；YAML 的 concurrent_limit 仅作为平台可用时的附加控制。
其他同步嵌套调度不在此锁保护范围内，部署前仍须检查 Worker 队列。

ASR 子调用等待 310 秒；L3 为 370 秒，大于其脚本 360 秒上限。
父脚本超时分别 1800/2100 秒。排队、实例故障和大规模候选扫描仍可能导致超时，须观察失败状态；不得为此自动重提已存在的付费任务。

## 配置与发布

复用 `f/content_research/research_db`、`media_storage_config`、`l3_worker_config`、
`asr_worker_config`、`automation_worker_identity`，密钥不入库文档或 Git。
日金额限制维持用户指定的不限额配置，不在此处恢复默认金额上限。

1. 暂停相关旧计划，核验运行中任务并备份计划定义。
2. 先发布依赖锁对应的代码、两个子 Worker、已加共享锁的 pending poller，再发布两个首次派发入口。
3. 三份 schedule 默认 disabled；`wmill.yaml` 不自动同步 schedule，沿用测试环境既有的单独 schedule 发布流程。
4. 经 JumpServer 核验双 Worker、配置、审核及当前版本，手动跑真实样本，核对输出和日费用后再启用计划。
5. 验证至少两个调度周期、争用时 deferred 后能再次执行、重跑零重复付费、结果入库和研究台展示。
6. 回滚先停首次派发计划；不可只回滚 poller 到无共享锁版本，同时保留新派发计划运行。

## 已验证与待验

本地隔离测试涵盖最新审核撤销不回退、候选门槛、历史任务跨模型去重、孤立费用、
过期证据、批次边界、脱敏异常以及真实 PostgreSQL 锁竞争和异常释放。
这些不代表服务器部署、双 Worker 现场运行、真实付费结果或用户业务验收已完成。
