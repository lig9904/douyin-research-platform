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

这不是整个 V1 或完整灾难恢复验收：未演练应用版本回滚；没有验证所有权限与运行配置能从备份独立重建，也未进行用户最终业务验收。
数据库备份不包含对象文件、环境文件或镜像；本次没有恢复/删除这些资源。
临时库已按授权删除，原备份保留，可重新执行恢复。

## 后续实物与归档引用校验（2026-09-21）

通过同一 JumpServer 终端只读校验，未重新创建恢复库：

- `media-content-20260921.log`（位于服务器 evidence 目录）：退出 0，`MEDIA_CONTENT_VERIFIED`，6 个对象、10,790,476 字节。对当前数据库引用的每个私有 MinIO 对象执行一次内网流式 GET，以实际字节重新计算 SHA-256 并比较大小；不是仅比较 HEAD 元数据。数据库读取使用只读事务。
- 3 个视频共 8,101,788 字节，3 段音频共 2,688,688 字节；6 项大小及摘要全部匹配。未落盘媒体、未调用采集/ASR/LLM、未写入或删除对象。配置从既有受控 Windmill 配置直接管道传递，未打印凭据。
- `media-archive-references-20260921.log`：退出 0，`ARCHIVE_MEDIA_REFERENCES_MATCH`。从原始 `research.dump` 只读导出 `media_asset` 的 COPY 数据，与当前数据库的 `id/kind/storage_location/bucket/object_key/content_sha256/size_bytes` 七字段逐行排序比较，6 条完全一致；因此归档中的这 6 个引用指向上述已验证实物。
- 引用清单指纹保留在服务器日志中：七字段制表符连接、行按字典排序并以换行连接（无末尾换行），计算 UTF-8 SHA-256。

运行环境准备曾遇到系统 Python 缺少 boto3、离线 uv 缺缓存；随后固定安装 boto3 1.43.98 与 psycopg[binary] 3.3.6 的独立 uv 环境成功，实际校验使用该环境的离线执行。未修改应用依赖或重启容器。

该证据证明上述备份的媒体引用及当前对象内容有效，不证明对象丢失后能从对象备份恢复，也不代表应用版本、完整权限配置或用户业务验收通过。
