# 本机安全验收：权限、HTTPS、日志与恢复

本方案是独立、一次性、仅本机的安全验收环境；它不复用 `l3-review-local` 的 Compose project、Colima profile、端口、named volume、环境文件或 TLS 私钥。

| 项目 | 本机安全验收值 |
| --- | --- |
| Colima profile / Docker context | `l3-security-local` / `colima-l3-security-local` |
| Compose project | `l3-security-local` |
| Windmill 入口 | 仅 Nginx `https://localhost:28443` |
| Windmill 明文端口 | 未发布；overlay 使用 Compose `!reset` 清除 base 的 `8000` 映射 |
| PostgreSQL | `127.0.0.1:25432`，只用于本机验收 |
| TLS | 每次本机生成的 7 天 localhost 自签证书，私钥 mode `0600`，在 ignored `work/local-security/certs` |

## 执行

```bash
scripts/local-security-env.sh init
scripts/local-security-env.sh start
scripts/local-security-env.sh verify
scripts/local-security-env.sh stop
```

`init` 生成 `.env.local-security`（mode `0600`）和 TLS 私钥，但不打印密码、token 或私钥。所有命令都显式使用 `docker --context colima-l3-security-local compose -p l3-security-local`，不使用当前 Docker context，也不读取真实 Provider Secret。

`verify` 覆盖三项：Folder ACL RLS、HTTPS/日志/限流、备份恢复。备份位于 ignored `work/local-security/backups/<UTC>`，目录 mode `0700`，包含 Windmill、研究库、globals、SHA-256 清单和非秘密 manifest。恢复演练只创建并随后删除精确命名的 `local_security_windmill_restore` 与 `local_security_research_restore`，不会写入源库。

## 可证明的权限边界

`verify-folder-acl.sql` 在新 Windmill CE 数据库中短暂创建 `.invalid` 合成 admin/reviewer/viewer 和 reviewer group，然后以 CE 实际的 `windmill_user` 角色及 `session.user` / `session.pgroups` 执行 `folder` 表已启用的 RLS policy。它验证：admin 看到 4 个 folder；reviewer 只看到 direct/group 两个；viewer 只看到一个 reader folder 且 UPDATE 影响零行。事务最终 rollback，不保留合成身份或 folder。

这证明的是 Windmill CE 当前数据库层 Folder ACL 策略与身份映射输入的隔离，不等同于完整的人类网页登录验收。CE 登录 session、邀请发信、企业 IdP 映射、浏览器 cookie/CSRF 和 UI 级别可见性仍需测试服务器的三个真实账号现场验收。生产权限绝不应通过直接写 Windmill 数据库配置。

## HTTPS、脱敏与限流

Nginx 仅记录 `remote_addr`、HTTP 状态、方法、无 query 的 `$uri` 和耗时；配置明确不记录 `$request`、query、Cookie、`Authorization` 或任意 request header。验证会携带随机 Authorization sentinel 并检索代理日志，发现 sentinel 即失败。

代理终止 localhost TLS，回源只走 Docker 私有网络；它同时设置安全响应头并为单一 loopback 来源给出 10 requests/s、burst 20 的基线。验证会建立证书链、确认日志未泄漏 sentinel，并并发请求直到确认至少一个 429。该数值是防意外本机刷请求的安全基线，不是生产容量结论。

## 服务器专属剩余项

- 受公司 CA/ACME 管理的正式证书、HSTS 正式 max-age、域名和反向代理可信 IP；
- 三个真实账号的登录、邀请、Folder UI、Secrets/Resources 不可见性和 `WM_END_USER_EMAIL`；
- 网络层 DDoS/WAF、集中日志保留和告警；
- 备份异机加密、保留策略、恢复 RTO/RPO 与灾备演练。
