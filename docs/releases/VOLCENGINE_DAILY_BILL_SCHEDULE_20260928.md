# 火山日账自动回补测试服验收（2026-09-28）

内部任务、作业与运行记录的精确定位保存在受控记录。

## 输入和部署边界

- 目标仅为 `test-research` 的研究库迁移 045、日账脚本、单个研究台 Raw App 和新日账计划；没有同步整个 Windmill 工作区，也没有启动付费采集。
- 测试服按 Draft PR #155 的固定提交部署，精确版本见受控记录。部署前后分别取得研究库与 Windmill 库备份，并各自完成隔离恢复；045 合同与既有两条全链路视频记录均通过恢复验证。
- 程序身份仍为专用账单只读身份；签名密钥只在 Windmill 加密变量中，未写入代码、文档或任务输出。

## 实际运行和持久化输出

- 首次真实 scheduled-mode 任务在 12.808 秒内完成。返回 `status=completed`、`snapshots_written=7`、`raw_provider_payload_included=false`；覆盖 2026-09-21 至 2026-09-27，不重放已经终账的日期。
- 测试研究库读回 7 条 `account_total` 日账快照，应付与已付均为 `0.000000 CNY`，`billing_finality=preliminary`；另有 7 条 `volc_billing_sync_gap`，均为 `resolved`、尝试 1 次、结果 `snapshot_written`。这些值是供应商当时返回的结算口径，不证明无调用，也不能作为最终出账金额。
- 研究台“运行与成本”读回“待核 0 天、有效快照 7 天”，逐日列出火山付款账户日账；TikHub 美元账单仍单列。当天尚未取得的账单继续显示缺失，不合成零费用。
- 之后只更新了这一条日账脚本，加入同日并发结果保护、数据库时钟判定及运行中付款身份固定；对应代码提交为 `588fdbd`、`03ba2e0`，Windmill 脚本版本已固定（精确哈希见受控记录），仍引用已固定的业务包提交（精确值见受控记录）。未重做数据库迁移、Raw App 发布或计划设置。
- 新脚本真实复测任务在 2.715 秒内完成，返回 `mode=scheduled`、`status=completed`、`snapshots_written=3`，覆盖 2026-09-25 至 2026-09-27，`raw_provider_payload_included=false`。这验证了更新后的正常同步路径；并发竞争及延迟失败覆盖只通过隔离 PostgreSQL 测试，尚未在测试服人为制造冲突。

## 自动计划验收

- 仅为 `f/content_research/collectors/sync_volcengine_daily_bill` 创建并启用一条计划。参数为 `billing_date=scheduled`、`coverage_start_date=2026-09-21`、测试研究库资源；时区 `Asia/Shanghai`，cron `0 0 18 * * *`。
- Windmill 页面预计下一次为北京时间 **2026-09-28 18:00**；数据库 `v2_job_queue` 与 `v2_job` 联表也确认同一路径的 `schedule` 触发任务已排队，`scheduled_for=2026-09-28 10:00:00+00`。这证明计划已生成，不等于首次自动触发已执行成功。
- 测试 Windmill 社区版忽略仅企业版支持的 `concurrent_limit`。日账脚本另有 PostgreSQL 事务级 advisory lock 和持久化缺口状态；隔离 PostgreSQL 测试已覆盖竞争与过期失败不覆盖新成功，但测试服真实并发故障仍需单独受控验收。

## 未完成和观察点

- 北京时间 18:00 后检查上述排队任务实际执行、次日计划是否续排，以及有延迟出账时的缺口回补；本记录不提前宣称自动周期已连续成功。
- 当前火山官方账单数据是付款账户范围、未终账口径，不能无分笔凭证地归因到九九项目或 LAS、ASR、L3 的各自实扣。
- 回滚先禁用这一条火山日账计划，再恢复已知脚本/App 版本；045 为加法迁移，不删除业务表或备份。
