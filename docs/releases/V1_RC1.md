# V1 RC1 发布说明

版本：`v0.1.0-rc.1`

Python package：`0.1.0rc1`

状态：GitHub Pre-release / 测试服务器候选基线

## 1. 这是什么版本

这是多平台内容研究平台 V1 的首个本机验收候选版。它固定一套可部署、可回滚、
可审计的代码与文档基线，供测试服务器现场验收使用。

它不是测试服务器已验收证明，也不是生产发布。Git tag 所指提交是唯一发布源；
部署时必须记录该 tag、完整 commit、镜像 digest 和目标环境证据。

## 2. 业务模块

### 采集与粗筛

- 输入：经预算门禁批准的 TikHub 发现页、账号、作品、视频和必要评论。
- 输出：带平台、来源、快照和规则版本的 PostgreSQL 规范化数据。
- 接口：`PlatformResearchProvider`、TikHub SDK 2.1.1、Windmill 采集 Flow。
- 验收：受限真实黄金链路已在本机完成；测试服务器需重新执行小流量 smoke 和成本对账。

### 研究台

- 输入：规范化视频、账号、热点、L0/L1 评分、任务和成本记录。
- 输出：首页、资产库、搜索、相似账号、监测、专题、收藏、筛选及审核视图。
- 接口：Windmill Full-code React App 与受控 backend runnable。
- 验收：本机浏览器读写与刷新持久化通过；真实 admin/reviewer/viewer ACL 待测试服务器验证。

### L2/L3 与媒体理解

- 输入：确定性特征、已完成隐私复核且与输入指纹绑定的证据包、人工批准预算。
- 输出：可追溯评分、结构化精研结果和录音文件转写。
- 接口：本地确定性规则、火山 Ark、火山录音文件 ASR。
- 验收：本机受控真实调用成功；默认失败关闭，不因发布而自动启用付费执行。

### Codex / MCP

- 输入：受限、缓存并经过治理的研究数据查询。
- 输出：白名单字段的搜索、详情、成本和七项 Windmill 研究工具结果。
- 接口：本机 stdio MCP 与 path-scoped Windmill HTTP MCP Gateway。
- 验收：本机真实业务库和短期 scope token 验证通过；测试服务器 TLS、reader role 和多账号 ACL 待验收。

### 部署与运维

- 输入：固定提交和镜像、目标环境 FQDN/证书、身份、Secret、备份和告警配置。
- 输出：五服务测试栈、结构化证据包、备份/恢复记录和回滚路径。
- 接口：Docker Compose、`scripts/test-server-*` 与 `test-server-evidence-v1`。
- 验收：本机 topology、隔离恢复与无外发监控契约通过；目标服务器必须完成
  [`TEST_SERVER_READINESS_V1.md`](../TEST_SERVER_READINESS_V1.md) 的全部清单。

## 3. 发布验证

发布准备提交必须在 GitHub 上完成以下三项检查：

1. `provider-tests`：Python 编译、单元测试和 PostgreSQL 集成测试；
2. `web-dashboard`：React bundle 与 Windmill Python backend 编译；
3. `infra-validate`：Compose、证据工具、部署/恢复脚本及隔离运行时检查。

三条 workflow 均可手动触发，以便在最终 release commit 上重新验证。真实付费调用不会由
普通 CI 自动执行。

## 4. 已知限制与协作门禁

- Issue #1：TikHub Provider 能力边界仍为 PARTIAL；限流 HTTP 细节和更多对照证据待补。
- Issue #5：测试服务器的 TLS、reader role、三账号 ACL 和审计留存待现场验证。
- Issue #7：自有账号发布效果闭环属于 V2，不阻塞本候选版。
- 测试服务器验收通过也不自动授权生产；生产 FQDN、身份、Secret、预算、RPO/RTO、
  告警值班和变更窗口需要独立确认。

## 5. 部署与回滚

部署前按 [`TEST_SERVER_READINESS_V1.md`](../TEST_SERVER_READINESS_V1.md) 记录基线并先备份。
仅从 Git tag 检出；不要从浮动分支部署。验证失败时停用 schedule/worker 和外部 runnable，
保留原库只读，并按已校验的备份执行恢复演练，不覆盖未知状态的数据源。
