# 测试服务器数据库隔离恢复证据

## 执行范围

用户明确确认后，经浏览器 JumpServer Web 终端执行；没有使用 SSH。
代码 `e7849cee1fc7259aeaf9023e6cd2687b7b31955d`（PR #101/#102 均已审查并通过 CI）。
恢复备份 `/srv/douyin-research-test/backups/20260921T024519Z`。
只创建 `test_server_research_restore`、`test_server_windmill_restore`；执行前查询两者数量为 0。
没有覆盖运行数据库，也没有调用供应商、修改或删除 MinIO 对象。

## 实际结果

- 日志：`/srv/douyin-research-test/evidence/restore-20260921-e7849ce.log`。
- 归档校验时间：UTC 2026-09-21 03:49:49；完成时间：03:49:57；耗时 8 秒。
- shell 退出码 0，输出 `RESTORE_DRILL_VALID`。
- `RESTORED_BUSINESS_AUDIT`：`business_chain_present`，`full_chain_videos=1`，`invalid_link_count=0`，`v1_release_accepted=false`。
- 关键表计数与备份归档一致；基础对象及恢复库所有者检查通过。业务审计核对音频、有效审核、转写、L3、费用与执行任务的关联及指纹。
- 脚本清理后再独立查询：指定临时库数量 0，`douyin_research`/`windmill` 两个运行库数量 2。
- 独立查看 Docker 状态：两个普通 Worker、native Worker、Windmill server、PostgreSQL 均保持 Up，server 与 PostgreSQL 为 healthy；未重启运行容器。

## 未证明的内容

这不是整个 V1 或完整灾难恢复验收：尚未核验恢复记录引用的 MinIO 实物内容，未演练应用版本回滚；没有验证所有权限与运行配置能从备份独立重建，也未进行用户最终业务验收。
数据库备份不包含对象文件、环境文件或镜像；本次没有恢复/删除这些资源。
临时库已按授权删除，原备份保留，可重新执行恢复。
