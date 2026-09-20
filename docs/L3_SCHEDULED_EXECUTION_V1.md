# L3 自动执行服务接入

服务层 `execute_reviewed_live_ark` 接受已持久化审核的视频、预期证据指纹/版本、Prompt 与受控 Ark 配置。它不是公开页面接口，也不是已部署的计划任务。

- 自动执行身份固定读取 Windmill 变量 `f/content_research/automation_worker_identity`，记录为该身份加 `/l3`。请求不能传 actor、审核员名单或任意任务 nonce；不依赖浏览器用户保持登录。
- 任务键绑定视频、证据指纹、模型 ID/修订、Prompt 和 Schema。证据加载与执行协调器仍核验数据库隐私审核，不由 Worker 自行批准。
- 未配置身份、审核缺失或证据变化时返回安全 blocked 状态，不构造供应商 Provider。Ark 密钥不进入配置 repr。
- 费用未知保留 unknown；服务端明确配置不限金额时可执行，已有显式额度仍按原规则执行，不恢复默认金额上限。价格是否已知与执行权限分开表示。
- 原手动离线预览入口保持禁止付费执行，明确 `execution_ready=false`，不因为有报价就宣称可以调用模型。

本地针对服务层、协调器和预览的 32 项测试通过，包含隔离 PostgreSQL 与合成 Provider；没有真实 Ark 调用。仍需固定配置的 Windmill 入口、真实模型结果、页面展示和连续调度验收。该服务的身份读取注入、Provider 注入仅供内部组合与测试，不能映射成用户可填写的公开参数。
