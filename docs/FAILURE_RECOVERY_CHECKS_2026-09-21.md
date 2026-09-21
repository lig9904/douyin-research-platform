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
