# Changelog

本文件记录面向使用者和部署者的版本变化。项目仍在进入首个测试服务器验收阶段；
Release、测试环境生效和生产生效是三个不同状态。

## [0.1.0rc1] - 2026-09-20

V1 本机验收候选版。此版本用于固定即将部署到测试服务器的代码与文档基线，
不代表测试服务器或生产环境已经上线。

### Added

- TikHub Provider、缓存、调用账本、预算闸门及受限真实数据黄金链路。
- PostgreSQL 多平台规范化数据模型、L0/L1 确定性评分、评论特征和账号相似度。
- Windmill 研究台首页、视频库、账号库、热点库、全局搜索、成本页及内部安全写操作。
- L3 证据组装、隐私复核、预算预览、结果绑定，以及 Ark 和录音文件 ASR 受控适配器。
- 本机只读 MCP 与七工具 Windmill HTTP MCP Gateway。
- 测试服务器部署、TLS/ACL、备份恢复、监控和结构化证据工具。

### Security and operations

- Provider 默认零重试、预算受限；真实或付费调用要求显式双门禁。
- L3 在隐私审核、输入指纹或响应绑定不完整时，于网络调用前失败关闭。
- 浏览器不持有第三方密钥；MCP token 不通过 URL query 传递。
- 测试服务器外呼 schedule 默认关闭；发布不自动创建 Secret 或修改生产环境。

### Verified locally

- Python 单元与 PostgreSQL 集成测试、Windmill React 构建和 Python backend 编译通过。
- TikHub、Ark、ASR 完成受限真实 smoke；TikHub → PostgreSQL → L0/L1 → Windmill
  黄金链路和研究台关键读写路径通过。
- 隔离环境的 localhost TLS、合成三角色 ACL、日志脱敏、双库备份恢复和五服务
  Compose topology smoke 通过。

### Known limitations

- 测试服务器的正式 CA/TLS、真实三账号 ACL、服务器 Secret、Provider 小流量 smoke、
  异机备份恢复和真实告警闭环尚未验收。
- TikHub 能力结论仍为 PARTIAL；Batch4 在 SDK 限流异常处零重试停止，未取得实际
  HTTP 状态码或 `Retry-After` 证据。
- ASR 真实调用成功，但供应商实际账单尚未对账。
- 快手、视频号、小红书、B 站和微博仅保留平台抽象，尚未接入 Provider。
- 本版本不得作为生产就绪声明。

[0.1.0rc1]: https://github.com/lig9904/douyin-research-platform/releases/tag/v0.1.0-rc.1
