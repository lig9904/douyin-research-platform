# 测试研究台应用回滚

## 状态与现场证据

2026-09-21 经 JumpServer 只读查询，`test-research` 中应用
`f/content_research/research_dashboard` 的 ID 为 2，最新版本为 29，历史保留 9–29。
版本 28 和 29 均能通过版本读取接口取得 `value.files` 与 `value.runnables`。
对比日志：服务器 `/srv/douyin-research-test/evidence/application-version-comparison-20260921.log`，退出 0。
两个版本的 policy 和 extra_perms 相同，但 runnables 不同；版本 29 中相对 28 内容变化的前端文件为
`/VideoLibrary.tsx` 与 `/src/components/MetricTimeline.tsx`。
随后只读检查定位到 5 个变化的 inlineScript 入口：`get_home_overview`、`get_video_library`、
`get_account_library`、`get_hotspot_library`、`get_video_metric_timeline`。需要逐项比较源码及依赖，不能仅回退前端。
这是回滚目标调查，不是回滚成功证据；文件比较尚不覆盖被删除文件，不能据此声明完整版本兼容。

## 回退单位

- 应用内容：完整 files、runnables、构建产物及当前实际版本，而不是只改 Git HEAD 或前端两个文件。
- 外部依赖：runnables 引用的脚本版本、包版本和数据库 schema，须逐项核对。应用历史版本存在不代表其依赖仍可执行。
- 服务/Worker：只有涉及镜像变更时才回退固定镜像 digest；不因为研究台内容回退而重启数据库或所有 Worker。
- 数据：默认保留现有业务库、审核、转写、分析、费用账本、MinIO 对象。加法迁移不得为了演练随意做 down migration。

发布机制核查：已安装 Windmill CLI 的 `pushRawApp` 会收集 files/runnables、生成 policy、构建 JS/CSS，
再通过 `apps/update_raw/{path}` 提交 multipart 的 app/js/css。因此只读取历史 JSON 或调用普通
`apps/update/{path}` 不足以证明 raw app 的浏览器构建产物已回退。演练应复用经核查的 raw app 发布流程，
并检查其生成 policy 与权限是否保持原有边界；不能为方便回滚增加权限。

## 实际演练步骤与通过条件

执行门：以下是待执行方案，不是发布授权或成功记录。写操作前须明确本次变更窗口、唯一应用 ID/path、
当前与目标版本、受影响计划清单、恢复当前版本方法及用户授权范围；若会中断用户使用或需新增授权，先确认。
目前调查候选为应用 2 的 29 → 28 → 当前内容恢复，不能把这两个版本号当成以后仍有效的固定参数。
禁止全工作区 sync/push；只允许本应用的 raw app 发布。数据库、MinIO、Secret 不属于本演练写入范围。

1. 重新读取当前应用版本，记录完整 Git SHA、schema 版本、镜像 digest、前后端版本及原 schedule 状态。导出当前应用和目标历史内容到受控 0600 文件，计算摘要；不导出或打印 Secret。
2. 比较 files 键集合及内容、runnables、policy/权限，检查依赖版本仍存在，旧查询兼容当前 schema。不能确认兼容时停止发布，先补证据或选择明确兼容的已验版本。
3. 对会被影响的任务停止新增派发并等待在途任务完成；不要关闭不相关的费用同步。界面演练期间不执行审核、采集、转写或模型写操作。
4. 明确并核查只针对本应用路径的 raw app CLI/API 参数后，将完整目标内容发布为新的应用版本；未形成精确命令不得执行。发布前再次核对 app ID/path、当前版本及步骤 1 快照内容摘要未被其他人改变；发生并发变更立即停止。预检查不是原子 CAS，必须确保发布窗口无其他写入者，不能宣称消除了竞态。保留所有历史版本，不直接改 Windmill 数据库。
5. 读取实际发布内容并核对目标摘要；浏览器验证同一真实样本的视频列表、详情、媒体、转写、L3 和每日费用读路径。记录版本 ID、UTC 时间、真实结果和失败项。命令退出 0 或页面返回 200 不等于读路径通过。
6. 将步骤 1 保存的当前内容重新发布，核对内容摘要及浏览器读路径恢复；仅恢复本次停用且原本启用的计划。核验运行任务状态，确认没有演练新增付费提交或丢失数据。
7. 保存回退及恢复两个方向的证据，报告实际不可用时间。任一方向失败都不得声明演练通过；优先用保存的当前内容恢复服务，不覆盖数据库。

## 仍未验收

尚未执行上述版本切换、浏览器复验和恢复当前版本；数据库隔离恢复、MinIO 实物校验不能替代这些步骤。
数据库真正切换、对象丢失恢复、完整权限/配置重建是不同范围，不由本应用回滚自动授权或证明。

## 真实数据库只读兼容性检查（2026-09-21）

通过 JumpServer 从 Windmill API 获取版本 28/29 的实际 inlineScript，在现有 Worker 的隔离 uv 环境运行，
不是用本机源码替代历史版本。数据库连接统一设置 `default_transaction_read_only=on`、
15 秒 statement timeout 和 10 秒连接超时；凭据受控管道传递，结果不输出正文。

- `application-read-compatibility-20260921.log`：两个版本各执行首页、视频库（指定真实案例详情）、热点库、指标时间线，共 8 次返回结构有效，退出 0。
- `application-account-compatibility-20260921.log`：从已部署脚本读取实际 `account_similarity` 依赖，在内存注册原模块后执行两个版本账号库，2 次成功，退出 0；不是 mock 相似度结果。
- `application-case-compatibility-20260921.log`：对既有案例 `da3062a7-9e87-480c-9c10-664a078aa779` 额外执行两版详情，分别断言 3 条来源证据、非空真实 ASR、非空 L3，并匹配提示版本 `ark-l3-evidence-boundary-zh-hans-v2-2026-09-21`，退出 0。

以上日志均位于服务器 `/srv/douyin-research-test/evidence/`。没有改变活动应用版本、业务数据或权限，没有调用付费 Provider。
证明的是这些输入下的真实数据库查询兼容性，不覆盖所有筛选/空数据分支、浏览器构建、播放器、历史 lock 安装或实际版本切换。

已保存受控源码快照 `/srv/douyin-research-test/evidence/application-rollback-29-28-20260921.json`：
766,241 字节，0600，独占新建、不覆盖旧文件。保存前确认活动应用仍是 29 且 value 与版本 29 一致；
包含 28/29 的完整 API 应用记录。该 JSON 不含已构建 JS/CSS，不能单独替代完整 raw app 发布/重建验收。
