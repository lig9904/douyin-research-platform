# 044：项目草稿版本与权限

状态（2026-09-28）：代码、隔离 PostgreSQL 测试和 PR #155 的 build/test/validate 通过；044 已发布测试服并完成真实研究台保存与回读。业务内容与评审结论留在受控记录，技术通过不构成业务验收。

## 输入、输出与边界

- 输入：当前 Windmill 登录用户的项目成员身份和经授权的项目草稿字段。用户邮箱只从 `WM_END_USER_EMAIL` 取得，页面传入的 `project_id` 仍由服务端成员关系核验；具体创意字段和值不在公开记录展开。
- 输出：项目内一份原创提案和只增不改的版本历史。状态仅有草稿、待内部低保真试片、已撤回；“待试片”不代表参考片已形成完整 Case、设定已获批准、素材权利已核清、已采用行动或已发布。
- 读取限当前有效项目成员；创建和修订限 Owner/Admin/Researcher。撤回是终止版本，不能再修订。跨项目不可读写。写入使用提交键幂等及预期版本检查；不调用模型或付费供应商，不创建项目行动卡。

## 接口、协作与验收

研究台增加项目专用草稿入口，提供创建、查看、追加修订与版本历史。后端单一 runnable `project_creative_concepts` 提供 `list/history/create/revise` 四个动作。草稿不自动转为正式行动、制作或发布决定；具体创意流程在受控渠道协作。

发布顺序：固定已推送代码提交；测试服双库备份；运行 044 加法迁移和发布契约校验；迁移前后归档分别做隔离恢复；仅发布研究台 Raw App；以项目 Owner 和只读成员身份回读页面、保存一条草稿、修订并核对历史与跨项目拒绝。发布前不得宣称测试服可用；发布后也不能仅凭页面出现就宣称九九内容方向已验收。

本地证据：`tests/test_project_creative_concepts.py` 与相邻项目决策/归档/发布测试合计 20 passed；React 应用 bundle 检查通过。另用独立 Chrome 无头页面实际执行了填写、保存、回读、追加修订、查看 v1/v2 历史及切换只读项目，截图已查看，保存在忽略目录 `tmp/creative-044/mock-concepts-saved.png`。此项本地 UI 测试使用模拟后端；真实测试服证据见下文。

2026-09-28 补充隔离 PostgreSQL 回归：持有 A 项目草稿 ID 的 B 项目 Owner 对 `history` 和 `revise` 都被拒；A 项目 Viewer 的创建、修订被拒。撤权不再用测试专属 `UPDATE` 模拟，而是调用生产 `member_revoke` 追加撤权记录，再核对被撤权者读、写和原创建键重放均被拒，版本数未增加。与协作测试合计 `7 passed`。这是本地权限状态机证据，不代替下文尚缺的测试服真实 Reader 验收。

## 测试服实证（2026-09-28）

- 服务器检出已通过 CI 的 `032a695f9`。迁移前双库备份为 `/srv/douyin-research-test/backups/20260928T000526Z`；执行 044 加法迁移后，研究库发布契约核验包含 creative concept contracts and ledger。迁移后双库备份为 `/srv/douyin-research-test/backups/20260928T001224Z`。
- 两份备份分别恢复到隔离临时库并清理。旧备份返回 `RESTORE_DRILL_VALID`、`creative_contract=legacy_absent`；新备份返回 `RESTORE_DRILL_VALID`、`creative_contract=present`。两次业务审计均显示 `invalid_link_count=0`、`full_chain_videos=2`；均未覆盖运行库，旧备份仍可用于回退结构。
- 复用本机钥匙串中已有的测试 Windmill 长期应用发布令牌，只对 `test-research` 的 `f/content_research/research_dashboard` 执行一次 `wmill app push`。CLI 回报 `Loaded 40 runnables`、`Raw app pushed`。远端应用回读含 `project_creative_concepts` runnable，策略仍为 `execution_mode=publisher`、`on_behalf_of=u/liguo9904`，原 A/B/C 三个测试用户的 Reader 项仍在；没有全工作区同步、其他脚本发布或付费供应商调用。
- 在真实 Chrome 研究台验证项目草稿保存、追加修订和版本历史；切换到隔离的验收项目后，前一项目草稿不可见，再切回仍可回读。具体项目名称、草稿数量与内容只留在受控记录；草稿不是正式行动、素材授权或发布批准。
- 尚未完成：正式内容的同口径观众反馈、角色/素材权利核查和自有账号结果。权限拒绝已在测试服单独验证；技术链路通过不能换算为业务可用百分比。
