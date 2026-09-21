# 失败恢复定向检查

## 本地隔离证据

在新建 PostgreSQL 库 `research_failure_drill_20260921` 加载 schema 与 001–018 迁移。
未使用现有共享测试库或服务器运行库：既有测试 `_clear()` 会删除测试表记录，故必须隔离。

定向执行 ASR/L3 coordinator、live ASR service、轮询上限及 Windmill 轮询测试，共 57 passed。
新增两项使用真实本地 PostgreSQL 持久化和合成 Provider：

- 轮询抛异常：保留原 submitted 任务和 `poll_failed`；新 coordinator 从数据库继续查询原 provider task，
  完成后只有一份 transcript、一份 completed 成本记录，未知 total_cost 仍为 NULL；原 submit 仅一次，
  恢复过程 submit 为零，完成重放不构造 Provider。
- 提交返回后、状态保存前注入 SystemExit：数据库保留 submitting；新 coordinator 返回
  `reconciliation_required`，不构造 Provider、不重提、不伪造转写或成本结果。

## 边界与后续

SystemExit 是进程退出路径的合成故障，不是实际杀死容器或真实网络故障。
这些测试证明协调器在所测边界的持久化/重放行为，不证明测试服务器受控失败恢复、调度唤醒或实际供应商任务恢复。
`reconciliation_required` 是禁止盲目重试的停靠状态，不等于自动恢复成功；缺少供应商任务标识时须核实供应商状态，不能重新提交来“修复”。
尚需按任务书完成服务器上的受控失败恢复证据，不把本地 57 项通过计入该项完成。

## 实际子进程终止测试与服务器执行入口

PR #116 已在 Linux 与真实 PostgreSQL 的 CI 中通过（完整套件 886 passed、6 skipped）。新增测试仅终止自己的合成 Provider 子进程：

- 提交返回、任务编号保存前 SIGKILL：数据库仍为 submitting，恢复必须停靠 reconciliation_required，不自动重提。
- 任务编号保存后、轮询中 SIGKILL：新协调器继续轮询原任务，最终仅一份转写及费用记录；完成重放不加载 Provider。

`scripts/test-server-asr-failure-drill.py --execute-isolated` 提供固定目标入口：

- `TEST_DATABASE_URL` 必须显式指向 `test_server_asr_failure_drill`；实际数据库身份再次核对，只读检查 source_video、research_task_cost、daily_budget 均为空，要求基础 ASR 表已存在。
- 固定 public search_path，仅执行上述两项 SIGKILL 测试；JUnit 中必须确实有两项非跳过、无失败的测试，不能把全部 skipped 算成功。
- 脚本不创建或删除数据库、不加载供应商密钥、不停止 Worker。建库、加载 schema/迁移和核验后删库必须另外按用户授权执行；不得对运行库执行测试的 `_clear()`。
- 服务器执行时临时报告目录通过 `TMPDIR` 放在 `/srv` 的 500G 盘。原始日志保存在服务器 evidence 目录。

本入口及 CI 不代表服务器调度恢复验收通过。专用临时库的服务器执行授权已请求；尚未执行。后续仍需明确区分进程/数据库恢复、Windmill 调度唤醒和真实供应商恢复证据。
