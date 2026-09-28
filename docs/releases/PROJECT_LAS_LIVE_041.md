# 041：项目内整片视频审核、单次付费派发与机器结果

状态（2026-09-27）：041 核心服务、Windmill 脚本和研究台页面已推送至草稿 PR #155，GitHub build/test/validate 已通过；本地合成视频的浏览器交互测试已通过。**041 数据库、三个 Worker 脚本和研究台单个 Raw App 已发布到测试服；默认禁用的计划任务已创建并回读。** Python 3.14 Worker 已通过不触发供应商的运行时探针；真实 041 LAS 付费链路、普通 Operator 权限和业务价值仍未验收。040 的四条历史 LAS 凭证不能充当 041 的实时链路验收。此文不宣布九九业务闭环完成。

## 输入与授权

- 项目 Owner/Admin 在“视频库”选择已接受、可用、已入私有对象存储的完整视频，核对准确的对象 SHA-256 和短期播放链接，并用独立的 LAS 审核版本确认交由火山作整片音画分析。音频 ASR 审核、公开视频纳入和人工 Case 都不能代替这项审核。
- 该审核只绑定一个项目、一个视频资产、内容哈希、交付域名、固定算子/模板/模型。保存审核不产生 LAS 调用；负责人再次点击“启动一次分析”才生成 `prepared` 任务。数据库记录审核人和付费请求人，其他项目或普通项目成员不能代为发起。
- 审核撤销会取消尚未提交的任务；已经提交到供应商的任务不会被伪称为免费或自动撤销。现有火山 LAS Key 保留在测试 Windmill Secret `f/content_research/las_video_understanding_api_key`，不得复制到浏览器、仓库或新变量。

## 输出、接口与费用

- 可信 Worker 只接收持久化的 attempt UUID；Submit 从服务器变量取账户范围、服务身份与媒体存储配置，校验私有对象、签发一小时 HTTPS 读取 URL，然后先持久认领 `submitting`，再向 LAS `Submit` **一次**。超时或未知回包保留 `unknown`，绝不自动重提。已得供应商任务号时，后续只用同一任务号 Poll；Poll 不依赖媒体存储变量。
- 10 分钟批调度可能与慢供应商作业重叠；同一 LAS 账户的批次用 PostgreSQL 会话锁串行化，重叠批次返回 `skipped_overlapping`，既不选任务也不启动子脚本。不同账户锁键隔离，进程结束即释放；这不替代 Submit 的持久原子认领。隔离 PostgreSQL 测试覆盖同账户重叠跳过、不同账户并行和释放后恢复。
- 如果 Worker 在持久认领后突然退出，15 分钟后调度用 `SKIP LOCKED` 跳过仍被真实 Submit 事务锁定的行，只把已失去运行者的超时 `submitting` 转成 `unknown`，保留 `submission_count=1` 且持续告警（最多列出 20 个任务 UUID，其余给出数量）。无供应商任务号时只能人工查账/查供应商任务，绝不自动再 Submit。隔离 PostgreSQL 测试覆盖活跃行锁跳过、跨账户隔离、超时隔离和禁止重提；这不是供应商真实故障恢复验收。
- 供应商任务号登记时间与任务创建时间分开；70 小时保护窗口从前者计算，避免排队导致首次 Poll 被误判过期。Poll 核对账户范围、任务号、模型用量和终态；完成后将私有机器摘要、结果指纹、不可变凭证写入项目库。它不是人工审片或可自动采纳的 Case。
- 研究台项目成员可回读任务状态和机器摘要，负责人可审核与启动。项目日账保留估价、未知费用和供应商分笔实扣三个不同口径；没有供应商账单分笔时不能称为实际花费。

## 发布与验收门槛

1. 使用测试服双库备份与隔离恢复流程迁移 041；核对已有 040 历史行不被覆盖。旧版未审核 `prepared` 实时行迁移为 `cancelled`，已认领/已提交/运行中的旧行转为 `unknown` 并标记人工对账，不能自动派发或伪造凭证。
2. 核验 Python 3.14 依赖锁与工作区哈希；只发布新 LAS 审核后端、三个 Worker/调度脚本和研究台单个 Raw App。配置非敏感 `project_las_account_scope`、`project_las_worker_email`，复用既有 LAS Key Secret。仓库 `wmill.yaml` 的 `includeSchedules: false`，因此脚本同步**不会**发布计划任务；必须单独用 `wmill schedule push f/content_research/project_las/run_pending.schedule.yaml f/content_research/project_las/run_pending` 发布为禁用，再用 `wmill schedule get` 回读路径、表达式、执行身份与 `enabled: false`。Worker 要求 Windmill 实际运行者与配置邮箱一致，批调度还要求 `WM_SCHEDULE_PATH` 精确匹配；先以普通 Operator 身份验证三个脚本无法直接运行、无法读取 LAS Secret，再配置计划任务以该邮箱运行。配置与权限核验后才可显式开启，并回读下一次运行时间。
3. 用一条已授权、确有完整画面和人声的项目视频：实际播放并审准准确资产 → 生成一个 `prepared` → 由调度只 Submit 一次 → 同任务号 Poll → 项目页、数据库凭证及日账回读。并验证撤销前派发、跨账户 Poll、重复触发、供应商超时和结果未知不会重复扣费。
4. 再由研究员核对完整声画、LAS 机器摘要和原帖，记录人工 Case 的事实、反证与适用边界；与可比样本及实际账号效果形成行动卡。只有这一步以及后续自有内容结果得到证据，才可讨论九九业务验收。

身份门槛参照 Windmill 官方的[环境变量说明](https://www.windmill.dev/docs/core_concepts/environment_variables)和[角色及权限说明](https://www.windmill.dev/docs/core_concepts/roles_and_permissions)：App 的实际查看者优先取 `WM_END_USER_EMAIL`，计划任务及普通脚本取 `WM_EMAIL`；计划任务路径由 `WM_SCHEDULE_PATH` 提供。代码校验不能替代测试工作区 ACL 的普通账号拒绝验收。

本地 Worker 身份校验现在先于数据库资源、LAS Key 和媒体存储配置读取。隔离测试确认非授权 `WM_EMAIL` 或 App 查看者身份下，Submit/Poll 不读取这些敏感配置；批任务遇到子脚本异常只记录任务 UUID、阶段和待核状态，不把异常中的签名 URL 或密钥写入调度日志，也不在同一批次重放提交。仍须用测试工作区真实 Operator 与计划任务身份复核 Windmill 权限及运行时变量。

项目成员的历史状态回读与负责人撤销审核现只用研究库，不读取 MinIO 配置或 LAS 账户变量；媒体配置暂时不可用时，已保存状态仍可查看、未派发审核仍可撤销。资产列表、试听、保存审核和付费准备仍须核对完整媒体配置与账户范围。后端隔离回归覆盖这一区分；真实 Windmill 运行态仍待发布后验收。

本地合成视频的浏览器交互回归覆盖播放前禁用审核、播放后人工确认、取消/确认付费请求、撤销、版本切换及播放失败。它只验证前端门槛，不是测试服真实视频播放或 LAS 供应商验收。运行时在 Raw App 目录执行 `node tests/ui-harness/build.mjs`；从仓库根目录启动端口 49321、根目录为 `windmill/f/content_research/research_dashboard.raw_app/tests/ui-harness` 的本地 HTTP 服务，再执行 `python3 windmill/f/content_research/research_dashboard.raw_app/tests/ui-harness/check_interactions.py`。截图保存在被忽略的 `tmp/las-041/`。

2026-09-27 补充：付费确认框在审核版本改变、刷新/切换资产、撤销审核或视频组件卸载时立即销毁，防止用户切换上下文后确认旧视频任务。合成视频浏览器回归已加入“打开确认框 → 切换版本 → 弹窗消失且未调用 `prepare`”；静态渲染测试、前端 bundle 和 `git diff --check` 通过。该交互验证不代替真实供应商派发验收。

041 发布脚本的数据库核验已扩展为检查 040/041 付费状态机及审核触发器的事件类型、启用状态和函数体 SHA-256；本机隔离 PostgreSQL 测试确认同名函数被替换为空实现、触发事件被改动或添加永不成立的 `WHEN` 条件时均拒绝通过。测试服已运行实际 `verify` 与新旧备份隔离 `restore-drill`，不再仅凭静态测试宣称恢复兼容。

同日测试服数据库现场：发布前备份用 041 脚本完成隔离恢复，摘要为 `las_live_contract=legacy_absent`；041 加法迁移自动备份，独立 `verify` 包括 030 主体档案、040 凭证及 041 实时审核契约，均通过。迁移前后 040 的尝试、结果凭证、账单分笔行数保持 `4/4/0`，新增审核及机器结果表均为 `0`。迁移后备份的隔离恢复摘要为 `las_live_contract=present`，两张恢复临时库已清理（残留数 `0`）；准确备份位置在受控发布记录。这只证明数据库发布与恢复，不代表脚本、计划任务、整片 LAS 供应商调用或九九业务验收。

同日 Windmill 现场：在测试工作区保存 LAS 账号范围与专用 Worker 执行身份变量；LAS Key 仍只使用既有加密 Secret。专用 CLI 令牌仅含 `scripts:write`、`schedules:write`、`users:read`，365 天有效，保存在本机登录钥匙串，未写入仓库。首次从仓库根目录推送脚本时，CLI 对无效远端路径返回退出码 0，**该次不计发布成功**；改从 `windmill/` 目录按精确路径推送后，三个脚本均回显 `Created new script` 与 `Script ... pushed`。远端逐项回读三个脚本均为非草稿 Python 脚本、依赖锁无错误；计划路径与目标脚本一致，时间表达式、时区及执行身份符合受控测试配置，且 `enabled=false`。精确执行身份与路径只留受控记录；计划任务未启动。

运行时探针：在 Windmill 以管理员身份运行 `poll_video`，传入测试库中不存在的合成 UUID。Worker 日志显示锁定依赖安装、进入 `Python (3.14) 代码执行`，最终 `ValueError: live LAS attempt unavailable`，符合服务先查任务、无任务不向供应商 Poll 的边界。运行前后历史尝试/凭证/账单分笔/041 视频审核/041 机器结果计数保持 `4/4/0/0/0`。精确运行定位保留在受控测试记录。该预期失败只证明测试 Worker 能安装并进入脚本及无任务不出站，不证明真实 LAS Submit/Poll、普通 Operator ACL 或业务价值。启用计划任务前仍需完成这些门槛。

研究台发布：使用此前已授权的应用发布令牌，仅执行 `app push f/content_research/research_dashboard.raw_app f/content_research/research_dashboard`，未同步整个工作区。远端单个 App 的回读 SHA-256 从 `2215366f2bbf638bb341bbe3ec170d273c816db6af2d05df9118877b8fb6700c` 变为 `bb85f10113d57f04d39ccba7c8628446e8b7f89858d7678ee72765d68ba04c32`，回读内容包含 LAS 模块。浏览器实测项目 A 的已接受视频《那一刻，我也被净化了》：旧转写和 L3 结果仍显示；新增“本项目整片音画分析”能加载入库整片（22.2 MB）、显示资产指纹与待审核状态，并打开可播放的整片审核预览。未保存 LAS 审核、未触发付费分析；能打开预览不代表整片人工核看、供应商响应或结果页面已通过验收。

普通成员现场权限抽查：在独立 Chrome 无痕会话依次登录既有测试身份 A、B、C；已在 Windmill 界面确认 A/B 为测试工作区 `Operator`，尚未独立回读 C 的工作区角色。A/B 直接访问已发布 `poll_video` 脚本得到 `Script not found`，C 直接访问 `dispatch_video` 和 `poll_video` 也得到相同拒绝，页面没有运行按钮；A/C 的 Variables 页面均为 `No variables yet`，没有显示 LAS Secret。C 能打开单个研究台 App，但页面明确显示无可访问项目；A 的项目选择只列“验收项目 A”。C 访问 `run_pending` 时页面空白且无运行按钮，**未得到可作为拒绝证据的明确错误**；变量页不可见也不等同于底层 Secret API 拒绝。故普通 Operator 的三脚本及 Secret 完整运行态权限门槛仍需补验，计划任务继续保持禁用。

2026-09-27 后续只读复核：在管理员 Chrome 会话打开测试工作区 `content_research` Folder 的成员面板，仅见受控管理员有 Folder 权限；未见 A/B/C 的 Folder 成员授权。计划详情显示目标路径、预设管理员执行身份、`Etc/UTC` 与既定十分钟表达式，更新按钮未启用。此为配置层旁证，**不是普通 Operator 的脚本执行拒绝或 Secret 值接口拒绝证据**，也不替代先前 CLI 的 `enabled=false` 回读。

同日补充项目切换竞态：项目或视频切换也会销毁旧付费确认框；旧项目的异步状态、资产、播放或审核回包不得覆盖新项目页面，状态回读按请求序号只采用最新结果。本地浏览器回归覆盖“打开确认框 → 切换项目 → 无付费请求”和“旧项目状态晚返回 → 新项目状态不被覆盖”。

同日上线前全量回归：以 Python 3.14 隔离 PostgreSQL 运行 `pytest --ignore=tests/test_python314_baseline.py -q`，退出码 0；这是排除锁门槛后的全量业务回归，不代表之后的工作树总测试已通过。随后用 `uv pip compile` 对四个固定提交依赖的 Python 3.14 入口生成锁，使用 Windmill CLI 1.815.0 的离线 `generate-metadata rehash` 更新三个脚本及单个 Raw App 的工作区哈希；`tests/test_python314_baseline.py` 与 `tests/test_project_las_windmill_contract.py` 现通过（数据库依赖项按环境跳过）。这不是 Windmill 远端解析或测试服运行时安装验收；完整回归仍需对最终工作树执行。

本次恢复与项目切换修复后，`tests/test_project_las_windmill_contract.py` 加 `tests/test_project_las_live_service.py` 在隔离 PostgreSQL 下 17 项通过，合成视频浏览器交互、前端 bundle、Python 编译及 `git diff --check` 通过。原本机 Windmill CLI profile 返回 `Unauthorized`；已有测试工作区长期应用令牌不含运行脚本/生成远端锁所需的 `jobs:run`。离线补锁解决了仓库文件完整性门槛，**没有**扩大该令牌权限，也不证明测试服可安装、可执行或 041 可发布。

2026-09-27 历史来源修正：核心提交 `9eaf26c` 的项目 LAS 状态接口增加 `record_mode` 和审核绑定布尔值；`1024b9f` 将四个 Windmill 入口统一固定到该提交并重新核验 Python 3.14 依赖锁。PR #155 的 build、validate 和 PostgreSQL test 全通过（测试汇总 `1345 passed, 6 skipped`）。发布前比对测试服单个 Raw App 的 57 个文本文件与 39 个后端 runnable，差异仅为本次 LAS 页面、测试、后端及其锁；随后只推送 `f/content_research/research_dashboard`，CLI 返回 `Raw app pushed`。远端应用版本 `75→76`，回读源码与锁均含 `9eaf26c`。Chrome 在项目页面回读到 `历史回填 · 已完成`、本项目审核未绑定的警示，并提示没有可引用的机器整片摘要；原估价仍明确是估价。该页面回读不代表 041 真实审核与 Submit/Poll 完成。三个 Worker 仍是此前已发布的版本；本机当前锁屏使已保存的专用脚本发布令牌不可读取，故本次**尚未**重发三个 Worker，计划任务仍保持禁用。

同日解锁后续：使用登录钥匙串内已有的专用脚本发布令牌，仅从 `windmill/` 目录重发 `dispatch_video`、`poll_video`、`run_pending` 三个脚本。CLI 对每个目标均返回 `Updated script` 和 `Script ... pushed`；远端逐项回读的 `content` 与 `lock` 都固定为核心提交 `9eaf26cad0989ce0f78e4d95eb27b0ad70598dc8`。计划回读路径与执行脚本一致，cron 为 `0 7-59/10 * * * *`、时区 `Etc/UTC`、执行身份为受控管理员，且 `enabled=false`。未创建新令牌、未改数据库或计划任务、未触发供应商调用；本次回读也不等于新依赖锁已在测试 Worker 实际安装运行。

随后在 Windmill 管理员页面重新载入已发布的 `poll_video`，以不存在的合成任务标识运行一次无付费探针。日志显示解析并从本机缓存安装固定提交 `9eaf26c` 的依赖、进入 `PYTHON (3.14) CODE EXECUTION`；结果为预期的 `ValueError: live LAS attempt unavailable`，回溯落在该提交的 `ProjectLASService.poll` 入口。代码在缺失任务处停止，未调用供应商 Poll。精确作业定位留在受控测试记录。此证据证明新版本 Worker 的运行时加载和缺失任务拒绝，不证明真实 Submit/Poll 或对象字节绑定。

## 2026-09-27 独立审查补充：付费前对象字节绑定

当前 `ProjectLASService.dispatch` 只调用 `PrivateS3MediaStorage.verify_object`；该函数用 S3 HEAD 比较 `Metadata.sha256`、大小与类型。对象键和数据库行按 SHA 寻址，但 HEAD 里的 SHA 元数据可随同键覆写一起伪造；当前签名 GET URL 也不含 `VersionId`。因此代码与现有测试**尚不能证明** LAS 实际读取的字节等于负责人试听并审核的对象。仓库没有测试桶版本控制、Object Lock 或禁止同键覆写策略的现场证据；既不能宣称这些保护不存在，也不能假设它们已生效。

在第一次真实付费 Submit 前，须只读核对目标桶的版本/不可变配置和所有可写主体的同键覆写权限；对选定的已审核视频执行完整对象 GET，流式计算 SHA-256 与大小并和数据库、审核指纹核对。若无法证明同键不可覆写，应实现并验证固定对象版本的签名 URL 或等效不可变快照；单次完整 GET 仍有校验至供应商下载之间的竞态，不能独自替代版本/不可变绑定。此门槛未满足时保持计划禁用，不以页面可播放或 HEAD 通过代替强字节绑定。

同日 AIStor 控制台只读现场：管理员打开测试媒体桶摘要和「Edit Bucket」预览；该桶当时选择 **Basic**，页面释义为「Single version and non-locking objects」，未选「Versioning」，摘要的「不可更改的」为「不」。随后点击 Back，没有保存任何配置。故此前“桶版本/不可变配置未知”现已收敛为：当时没有可供 041 审核绑定的对象版本或锁定能力，不能直接以裸 Key 签名 URL 做付费整片 Submit。须先确定存储方案并现场验证已审核字节、签名 GET 和供应商下载路径；不能因能登录控制台就视为此门槛通过。

经用户确认后，同日将测试媒体桶从 Basic 改为 Versioning，未启用文件夹/前缀排除；控制台返回「存储桶已成功更新」，摘要回读「版本控制：已启用」「不可更改的：不」。隔离对象经同键两次上传，列表回读两个版本且总字节数正确；隔离键与版本号保留在受控测试记录，未删除或覆写业务对象，也未开启计划任务。控制台选中旧版后右侧详情仍显示新版，故当时还不能把 UI 选择当作旧版本可读取的证据。版本控制启用本身不构成付费放行。

随后用测试工作区已有的桶专用存储变量执行只读、零付费探针：同一隔离键指定旧/新版本的 S3 `GetObject` 均成功，响应版本和字节摘要分别与预期一致。同一 IAM 凭证生成的公网 HTTPS、指定版本的预签名 GET 均返回 HTTP 200，字节与内部 GET 相同；脚本输出未包含密钥或签名 URL。精确键、版本、摘要与作业定位保留在受控测试记录。这证明当前测试 IAM 可读取两个隔离版本，不需为本次测试另增旧版本读取权。**仍未**验证历史业务对象的非 null 版本、前端真人核看与 042 审核/Submit 的部署链路；LAS 计划任务保持 disabled，不能把隔离对象探针当作真实视频业务验收。

另核对批调度的终态语义：供应商返回 FAILED/TIMEOUT 时，Poll 会保存 `failed` 不可变凭证。2026-09-28 修正批任务分类：`failed` 单独报 `known terminal failures`；只有 `unknown`、运行异常及无 task ref 的旧提交进入 `needs reconciliation`。混合批次同时列两类，不触发重复 Submit。本地契约测试及 PR #155 的 test/build/validate 均通过。使用已有钥匙串令牌只重发测试服 `run_pending` 单脚本，远端源码摘要与本地一致；计划任务回读仍为 `enabled=false`、既定十分钟表达式及受控执行身份。精确源码摘要和身份保留在受控测试记录。这不是启用调度或真实供应商批结果验收；启用前仍需以真实凭证复核。
