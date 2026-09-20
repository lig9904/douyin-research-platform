# V1 本机全链路验收记录（2026-09-20）

本记录只证明同一台开发机上的隔离环境与受限真实接口 smoke；它不代表已部署测试服务器、
生产生效、真实 IdP 多账号 ACL、正式证书或异机灾备。Secret、响应正文、媒体 URL、供应商
任务 ID、真实账号邮箱和浏览器截图均不进入仓库。

本轮最终集成回归绑定 GitHub `main` 提交
`f4e298b814aef28d24625f01883c2c1235c72ca1`（PR #43、#44 均已合并）。

## 1. TikHub → PostgreSQL → L0/L1 → Windmill

- 使用低粉榜与批量详情的受限黄金入口，零重试、最多 5 条、最多 2 次未缓存调用、上限
  0.01 USD。
- 实际 run：`5bd7e419-bb5a-44ce-a86c-e075d73acc36`，输入 2、规范化视频 2、L1 评分 2。
- 本轮账本为 2 次 Provider 调用，其中发现页命中已审阅缓存，批量详情 1 次未缓存外呼；
  实际记账 0.001 USD，未超过 0.01 USD。
- Windmill 首页与视频库能读取上述真实数据；后续 live test 使用 `--force-refresh` 时会直接返回
  本轮 cached/uncached 聚合计数，并要求至少 1 次本轮未缓存调用，避免历史账本冒充 live。

## 2. 研究台内部写操作与浏览器刷新

在本机 Windmill App 中对真实视频、真实账号和明确标记为 `local-fixture` 的合成热点完成：

- 三类资产加入团队共享监测；
- 三类资产加入同一用户专题；
- 视频加入个人收藏；
- 保存视频筛选；
- 刷新页面后重新读取数据库状态，监测、专题、收藏与筛选仍可见。

在服务器 allowlist 变量配置后又通过浏览器执行 1 次监测写入并看到成功回执。最终脱敏聚合：
9 条动作审计、2 个用户 collection、4 个 collection item、1 个保存筛选，
视频/账号/热点各 1 个处于监测队列；所有动作聚合 `external_calls=0`、`llm_calls=0`。
团队监测是共享工作流；专题、收藏与筛选按 actor 隔离。仅有 Viewer 权限的用户不得获得写后端
执行权限，测试服务器需用真实 admin/reviewer/viewer 账号现场验证 Folder ACL。

账号库的确定性相似度已在同一套本机 PostgreSQL / Windmill 运行时验收：临时插入同平台候选后，
页面显示 100 分、100% 证据覆盖和匹配领域，随后精确删除临时候选。回归测试同时覆盖空白字段
归一、失效及非法 UUID 安全降级，以及最优候选位于第 200 个排序 ID 之后仍可被发现。

Windmill HTTP MCP Gateway 已用 15 分钟、workspace-bound、仅含
`mcp:scripts:f/content_research/research_tools/*` 的临时 token 验证：七项业务工具逐项调用成功，
`limit=51` 固定拒绝，`runScriptByPath` 跨目录运行 `analysis/manual_l3_preview` 被 scope 拒绝；
无 Provider/LLM/付费调用，临时 token 和凭据文件均已撤销或删除。

## 3. Volcengine Ark 与录音文件 ASR

- Ark：固定官方 origin、禁止重定向、零重试；1 次真实结构化调用成功，响应绑定与 schema
  校验通过，估算 LLM 成本 0.0040176 CNY。
- ASR：使用官方公开 m4a 示例；1 次 submit、2 次 poll，终态 `completed`，收到转写且分段数为 1，
  `provider_error=false`。供应商实际账单尚未对账，因此不把 ASR 成本写成已知值。
- 两次 smoke 均只输出聚合事实；密钥通过本机内存注入，未写入 Git、证据文档或命令输出。

## 4. 本机 HTTPS、ACL 与备份恢复

独立 `l3-security-local` Colima/Compose 环境已执行 `scripts/local-security-env.sh verify`：

- localhost 自签 TLS 校验成功，明文 Windmill 端口在 overlay 中被移除；
- 合成 admin/reviewer/viewer 的 Windmill CE Folder RLS 可见性与 Viewer 不可写检查通过；
- Authorization 与 query sentinel 均未进入 proxy 日志；并发探针观测到 HTTP 429；
- Windmill 与研究库备份、SHA-256 校验、临时双库恢复、行数哨兵比较均通过；
- 恢复演练结束后两个固定临时数据库数量为 0。

已有业务库另通过 `LOCAL_RESEARCH_MIGRATE=YES scripts/local-l3-env.sh migrate` 完成重名/非法目标
预检、先备份，并以研究数据库账号在单事务内应用全部 migration；新增表归属、读写权限以及
研究台表/列/约束/索引验证均通过。

使用该次备份运行 `LOCAL_RESEARCH_RESTORE_DRILL=YES scripts/local-l3-env.sh restore-drill <backup-dir>`
后，SHA-256、关键表行数、owner 与临时库清理均通过；该演练从未覆盖 `douyin_research`。

当前 `main` 又在独立 `l3-security-local` Docker context 执行一次一次性测试服务器 Overlay 烟测：
PostgreSQL、Windmill server、普通 worker、native worker、TLS proxy 五个服务全部健康；仅代理发布
loopback 临时端口，明文 HTTP 被拒绝，PostgreSQL/Windmill 无直接宿主端口，proxy 只读根文件系统、
最小 capability 与日志 Authorization/query sentinel 脱敏均成立。脚本退出后精确 Compose project、
volume 和临时目录已清理；此项仍只是本机拓扑证据。

## 5. 自动化检查

- GitHub `main` 对应代码树的 Python 单元与 PostgreSQL 集成测试：404 passed、5 skipped，
  真实付费测试默认跳过；
- Windmill React App：`npm ci` 与 esbuild bundle 通过；Python inline backends 编译通过；
- Windmill metadata：账号相似度 helper、MCP helper/七工具与 Raw App 均为 up-to-date；
- 安全 Compose overlay：五服务一次性启动与清理实测通过；shell syntax 与 `git diff --check` 通过；
- Ark/ASR/TikHub live smoke 需要显式环境双门禁，GitHub CI 不携带 Secret、不会付费外呼。

## 6. 测试服务器仍需完成

只有拿到目标服务器、域名与身份方案后才能验收：正式 CA 证书与域名、真实 IdP 三账号 ACL、
服务器 Secret 注入与轮换、服务器到 TikHub/Volcengine 的出口、计划任务与并发、异机备份恢复、
监控告警，以及最终小流量回归。上述事项不能用本机结果替代。
