# T1 每日费用上线记录

## 范围和部署

- 测试服工作区：`test-research`；CLI 配置名：`test-server`。
- 部署提交：`2a37a8ce86ee35fcbf70c91c11a7527603c82436`（PR #67）。
- 应用路径：`/srv/douyin-research-test/app`。部署前保存研究库和 Windmill 库的 custom-format 备份；本次未验证恢复，不能据此通过 T6。
- 以业务库所有者应用迁移 014、015；只发布每日费用脚本和研究台 raw app，没有全量覆盖 Secrets 或其他脚本。
- 费用同步仅调用供应商免费用量查询接口，不启动视频采集或模型调用。

## 三方一致性验收

首次手动同步的供应商响应、数据库和实际浏览器页面均已核对：

| 项目 | 值 |
| --- | --- |
| 供应商 / 账户 | tikhub / default |
| 供应商账期 | 2026-09-20 |
| 时区 | America/Los_Angeles |
| 累计费用 | USD 0.052000 |
| 请求数 / 付费请求数 | 4 / 3 |
| 抓取时间 | 2026-09-20T16:20:25.463671+00:00 |

浏览器首页显示 `tikhub USD 0.052`；运行与成本页显示 `0.052 USD`、`4（付费 3）`、`当前账期累计`、`09/21 00:20 已同步`。本地日期与供应商账期不同是时区差异，不强行改写供应商日期。费用为账户累计值，不代表平台内两条 Provider 记录的逐笔实扣归因。

## 定时同步

版本化计划文件：`windmill/f/content_research/collectors/sync_daily_spend.schedule.yaml`。
仓库默认 `enabled: false`，防止未核对目标环境时意外启用；部署确认数据库资源、现有供应商 Secret 和脚本就绪后，显式执行：

```sh
wmill schedule push f/content_research/collectors/sync_daily_spend.schedule.yaml f/content_research/collectors/sync_daily_spend --workspace test-server
wmill schedule enable f/content_research/collectors/sync_daily_spend --workspace test-server
```

已确认工作区仅有这一条同路径计划，数据库 `enabled=true`，UTC 每小时整点触发。通过 `v2_job_queue` 与 `v2_job` 联表确认下一项：

- job：`01a0bf9f-c47e-5a9c-9bb2-cf66107e3817`
- scheduled_for：`2026-09-20 17:00:00+00`
- trigger：`f/content_research/collectors/sync_daily_spend`
- trigger_kind：`schedule`

排队不等于执行成功；尚需实际调度完成记录和连续周期观察。不要为验证调度而重复创建计划或加速收费业务任务。

## 尚未完成与回滚边界

- 火山实际日账仍需接入；未同步不能当作零费用。
- 首页旧预算使用率已在 PR #71 修复并部署：提交 `d667f6fdb704e3db055f2a960454bd6dd0feb1cf`。实际浏览器核对四张指标卡片、供应商 USD 0.052、导航“运行与成本”及“运行状态与调用”；不再把两次请求护栏显示成 100% 金额预算。账号粉丝数的上游零值质量仍待独立核实，不粗暴改写真实零值。
- 回退应先禁用这个唯一费用计划，再恢复已知旧应用/脚本版本；迁移为新增账本结构，不通过删除业务表回滚。备份恢复必须在隔离目标验证。
- 本记录证明 T1 的 TikHub 手动同步、页面和计划入队，不证明 T2–T6 或整个 V1 已完成。
