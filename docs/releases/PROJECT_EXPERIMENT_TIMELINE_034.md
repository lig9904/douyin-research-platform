# 034 行动实验时间线修复：发布候选

状态（2026-09-25）：本地完整 pytest 套件在隔离 PostgreSQL 上通过（退出码 0）；034 迁移重放和时间线正反例通过，前端 esbuild、Python compileall、Shell 语法和 `git diff --check` 通过。测试服研究库已应用 034 并验证；单个研究台 Raw App 已发布为 Windmill 版本 51。A/B/C 真实账号的写接口**权限边界**已验证，但未保存有效行动卡，也未完成三身份前端或九九真实业务验收。033 的测试服结构和只读权限验收记录见 [TEST_SERVER_033_20260925.md](TEST_SERVER_033_20260925.md)。

## 测试服数据库阶段证据

- 发布前工作树干净，原 HEAD `b3451261`；033 迁移合同 `verify` 通过。前备份 `/srv/douyin-research-test/backups/20260925T071559Z` 独立校验为 `BACKUP_VALID`，manifest SHA-256 `b171dc9ed69e3cb39fe07ebc6387533e5dab2702f9ec03cba161dc411e570f2b`。
- 固定候选提交 `111309f5` 的 GitHub build/test/validate 全部通过后，测试服切换到该提交。`migrate` 仅应用缺失迁移并先自动备份到 `/srv/douyin-research-test/backups/20260925T071703Z`；随后独立 `verify` 对迁移账本及 034 函数摘要返回 `VERIFIED`。
- 迁移后备份 `/srv/douyin-research-test/backups/20260925T071749Z` 独立校验为 `BACKUP_VALID`，manifest SHA-256 `819cb33b09b504d9d62384a5a2e46e48565197a9d32e3404dccf714fcb7cd97a`。双库隔离恢复返回 `RESTORE_DRILL_VALID`、`timeline_contract=present`、`invalid_link_count=0`、`v1_release_accepted=false`，耗时 21 秒；临时库由脚本清理，运行库未被覆盖。
- 经用户确认，在测试 Windmill 创建 1 小时有效的单应用发布令牌，仅授予 `apps:write`、`raw_apps:write`、`users:read`。本机 `wmill app push` 仅发布 `f/content_research/research_dashboard`，返回 `Raw app pushed`；刷新 Windmill 编辑器后，部署历史从版本 50 增至版本 51。公开研究台可按原管理身份选择“验收项目 A”并进入“行动复盘”页面。用户选择**不立即撤销令牌，等待 1 小时自动过期**；不在仓库或文档记录令牌值。
- 用 `dy-a`、`dy-b`、`dy-c` 三个真实 Operator 账号分别登录 Windmill，读取 Version 51 App 自身的 `mutate_project_decision_loop` 内联脚本，通过应用组件接口提交独立随机幂等键的 `create_card` 空参数。A→A 与 B→B 均进入业务校验并返回 `ValueError: create_card payload is invalid`；A→B、B→A、C→A 均在业务校验前返回 `PermissionError: RESEARCH_PROJECT_WRITE_DENIED`。测试任务 ID 分别为 `01a0d786-6061-a49c-54ae-7760ecd8360c`、`01a0d787-c3c9-97d2-fcc8-742ae08616b3`、`01a0d787-1eb9-ea86-bf63-638b86cea7a2`、`01a0d787-216c-c86e-a345-10e89c6d375e`、`01a0d787-2430-562e-1eef-ea944e94dbbf`。各账号测试后退出；故意不给合法业务字段，未创建行动卡或伪造发布。

## 输入、输出与边界

- 输入：项目内已接受的公开视频、已创建并锁定的行动卡；人工填写的实际发布时间（带时区）、平台、公开账号、作品 ID 和内容审核版本；带来源时间的非个人日汇总。
- 输出：034 加法迁移将 `source_video_id` 和行动卡 `created_at` 纳入不可变条件，新增 `project_publication_record.published_at`。新发布记录要求申报发布时间不早于行动卡创建时间、不晚于当前时间，且与上海时区发布日期一致。原有 033 发布记录保留 `published_at=NULL`，不追认为已证实的事前实验。
- 复盘：必须等 `published_at + observation_window_days` 完整届满；被选报表的统计日和来源报告时间也须到达观察窗末端。历史发布记录若没有实际发布时间，不得以新规则形成复盘。
- 证据等级：平台账号、作品 ID、发布时间目前均由项目成员人工声明，**尚未向平台核验真实性或账号归属**；研究台页面明确标注。不能把“已登记”写成“真实发布已核验”。

## 接口与协作

- `create_publication` 增加必填 `published_at`，ISO 8601 且带时区；`publication_date` 仍为上海业务日期。调用方需与 034 数据库迁移同批发布，旧版页面/后端不得混用。
- `get_project_decision_loop` 返回 `published_at`；页面显示人工登记时间与证据等级。Windmill 发布只针对 `f/content_research/research_dashboard` 单个 Raw App，继续使用原有项目 ACL，不扩大脚本、资源或 Secret 权限。
- 034 测试服发布已经完成备份、迁移账本与触发器函数验证、双库隔离恢复及单 App 部署。A/B/C 的真实写接口权限分支已验证，但空参数失败任务不能替代有效写入、页面操作、状态转移和结果回读；下一阶段仍须用真实项目行动验证这些业务步骤。正式行动和发布只能使用真实业务材料；不得为测试伪造九九发布。

## 验收

1. 隔离 PostgreSQL 正反例：重复应用 034、已锁卡换绑另一个本项目已接受视频被拒、早于建卡的发布在应用和数据库两层被拒、完整观察窗前/来源报告过早的复盘被拒，旧记录保持可读。
2. 新旧数据和前端契约：新记录必须有带时区的发布时间，旧记录显式展示“未登记”，无自动回填；最近审计显示后端降序列表的最新事件。
3. 测试服发布与回读：仅在获准发布窗口内迁移并发布，核验脚本/页面版本、A/B/C 真实账号的项目隔离和普通身份写路径；缺少真实官方账号与作品时不能验收业务结果。
