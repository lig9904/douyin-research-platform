# MinIO 对象丢失恢复演练

## 范围

本演练只使用 `douyin-research-media` 桶中新建的隔离前缀 `recovery-drill/<run_id>/`，创建 `target.bin` 和 `snapshot.bin` 两个 4096 字节测试对象。不得列举全桶、修改 IAM/桶策略、读写数据库或接触已有媒体对象。

工具 `scripts/minio_object_recovery_drill.py` 要求显式 `--execute`、HTTPS endpoint、精确 bucket 和 0600 的普通凭据文件；凭据文件不得是符号链接。恢复验证完成前的失败会保留已创建对象供调查；只有完整恢复及摘要复验成功后才进入清理。若清理阶段本身部分失败，工具会明确报告 `cleanup=partial` 和两个对象的已知状态，避免误报“均已保留”。

## 本地验证

`tests/test_minio_object_recovery_drill.py` 覆盖成功、失败保留、对象碰撞、内容损坏、凭据权限、执行门、错误脱敏和 endpoint 校验。与既有媒体存储测试合并运行共 27 项通过；脚本 compileall 和 `git diff --check` 通过。

## 真实 S3 演练结果（2026-09-21）

- endpoint：`https://minio.yudao.cc:6443`
- bucket：`douyin-research-media`
- region：`us-east-1`
- run_id：`1efcf13912714b86b765a2d037be064c`
- 字节数：4096
- SHA-256：`9086e202317bed386ef180b8f1e50ef90049c9a1a94a3291e7e63726183522a3`
- 测试目标：`recovery-drill/1efcf13912714b86b765a2d037be064c/target.bin`
- 测试快照：`recovery-drill/1efcf13912714b86b765a2d037be064c/snapshot.bin`

实际流程完成：上传目标并校验 → 复制为快照并校验 → 删除目标并确认不存在 → 从快照恢复目标并重新校验大小、摘要和内容类型 → 删除两个演练对象并确认不存在。输出 `MINIO_OBJECT_RECOVERY_DRILL_PASSED`，没有触碰已有媒体对象。

这证明当前凭据、反代和桶可以完成隔离对象的丢失恢复；不等于已有业务媒体已从离线备份恢复，也不证明整桶灾难恢复、IAM 重建或应用回滚。正式媒体丢失时仍须先依据数据库对象 key 和可信备份定位精确对象，校验摘要后再恢复，禁止用全桶覆盖代替精确恢复。
