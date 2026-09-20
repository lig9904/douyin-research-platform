# 测试服务器验收检查点

日期：2026-09-21。范围：现有浏览器 JumpServer 会话中的服务器操作与数据库只读核验。不是最终 V1 验收。

## 已部署

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
