# V1 测试服务器部署与验收 Runbook

日期：2026-09-20

## 0. 目的与边界

本手册用于把已经完成本机验收的 V1 部署到**隔离的测试服务器**，并在真实域名、真实登录身份和真实网络出口下取得可复查证据。它不是生产发布手册，也不是把本机 Compose 环境直接暴露到公网的操作说明。

| 环境 | 可以证明 | 不能替代 |
| --- | --- | --- |
| 本机 | 代码、迁移、受限 live smoke、浏览器功能、localhost TLS 与合成 ACL/RLS 演练 | 域名证书、真实多账号登录、服务器出口、异机恢复 |
| 测试服务器 | 测试域名、三类真实账号 ACL、服务器 Secret 注入、低流量 Provider、计划任务、备份恢复与告警链路 | 生产容量、生产数据保留、生产身份提供方或正式变更授权 |
| 生产 | 经单独发布批准后的真实业务结果 | 不能由本机或测试服务器结果自动推断 |

执行者不得将测试域名、测试数据库、测试 Secret 或测试采集数据复用于生产。没有明确的目标服务器、域名、身份方案和备份落点时，可以完成代码审查与部署包准备，但不得猜测这些参数或启动外呼任务。

## 1. 输入、输出与协作

### 1.1 部署前由环境负责人提供

| 输入 | 责任方 | 验收用法 |
| --- | --- | --- |
| 测试服务器 SSH/控制台访问与操作系统基线 | 基础设施负责人 | 安装/运行容器、检查防火墙、保留审计记录 |
| 测试 FQDN、DNS 管理方式、证书签发方式 | 域名/网络负责人 | 配置反向代理 HTTPS 与浏览器回调 URL |
| 仅测试使用的 admin、reviewer、viewer 三个真实身份及登录方案 | 身份负责人 | 验证登录、Folder UI、应用权限和操作人归属 |
| 备份异机/对象存储落点、加密与保留策略 | 基础设施负责人 | 验证可取回、校验、恢复 RTO/RPO 证据 |
| 允许的出站域名、代理与 DNS 规则 | 网络负责人 | 验证 TikHub、Ark、ASR 的最小连通性 |
| 每个 Provider 的测试 Secret 与测试额度/价格版本 | Provider 负责人 | 只通过 Windmill Secret 注入并执行一次有上限 smoke |

这些输入必须通过现有安全渠道提供；不写入 Git、Issue、聊天记录、`.env.example` 或命令输出。部署操作者应回传脱敏的版本、时间、状态与审计 ID，而非 Secret、token、响应正文或媒体 URL。

### 1.2 交付输出

部署完成后应形成以下可审计输出，并放在受控运维证据位置：

- 已部署 commit SHA、镜像 digest、Windmill 版本、迁移版本与同步结果；
- 测试域名 HTTPS、DNS 解析、明文入口关闭和安全响应头的检测记录；
- 三个真实账号的 ACL 用例结果与 `WM_END_USER_EMAIL` 操作人审计样本；
- 数据库备份清单、校验结果、异机落点、恢复演练结果、实际耗时；
- Provider 小流量调用账本：次数、上限、日期、成功/失败及实际/待对账成本状态；
- 计划任务、并发和失败告警的触发与关闭记录；
- 失败回滚记录（如发生）：触发条件、恢复点、数据影响与复验结果。

### 1.3 脱敏证据包

上述输出必须汇总为受控的 `test-server-evidence-v1` JSON，而不是只留截图或口头结论。先在权限为 `0700` 的证据目录初始化一个严格 `0600`、全部 `not_run` 的模板；镜像参数必须使用实际的 digest 引用，不能使用 tag：

```bash
install -d -m 0700 /srv/douyin-research-test/evidence
scripts/test-server-evidence.py init \
  --output /srv/douyin-research-test/evidence/acceptance.json \
  --commit-sha <40位已部署提交SHA> \
  --compose-project douyin-research-test \
  --fqdn <测试FQDN> \
  --postgres-image <postgres镜像@sha256> \
  --windmill-image <windmill镜像@sha256> \
  --proxy-image <nginx镜像@sha256>
```

证据结构 Schema 见 [test-server-evidence-v1.schema.json](test-server-evidence-v1.schema.json)，固定的 19 个检查以对象键表达，因此无法重复或漏项。它只允许固定状态、UTC 时间、`ev_...` 脱敏证据 ID、计数/耗时、SHA-256 和聚合成本状态；没有自由正文、URL、邮箱、请求 ID、媒体地址或 Provider 响应字段。备份、双库恢复与 globals 恢复必须回填同一归档的 manifest 指纹，globals 还必须匹配 inventory 指纹；计划安全/并发只能绑定本地监控契约，告警闭环则必须另有外部投递证据和严格递增的触发、确认、关闭时间。获准 Provider 才标记 `approved=true`；未获准项必须保持零调用、无成本。Schema 用于工具兼容和结构审阅，下面的 Python 验证器还执行跨字段指纹一致性、时间顺序、敏感值启发式拦截与最终完成态检查，是封存前的权威入口；不得仅凭通用 JSON Schema 校验声称通过。每次更新后执行：

```bash
scripts/test-server-evidence.py verify \
  --input /srv/douyin-research-test/evidence/acceptance.json
```

只有所有必需检查通过、每个获准 Provider 恰好一次受控 smoke 后，才执行最终验收与封存；封存不会输出证据正文，只另写 SHA-256：

```bash
scripts/test-server-evidence.py seal \
  --input /srv/douyin-research-test/evidence/acceptance.json \
  --output /srv/douyin-research-test/evidence/acceptance.sha256 \
  --require-complete
```

验证器会拒绝额外字段、重复/缺失检查、非 digest 镜像、保留域名、权限不是 `0600` 的文件以及疑似 Authorization、token、Secret、邮箱、URL/query 或正文值；错误输出不回显可疑值。`verify` 通过仅证明格式和边界合规，不能代替各门的真实执行证据。

## 2. 拓扑与接口边界

测试服务器只使用当前仓库的固定镜像 digest 与双数据库边界：Windmill 内部库 `windmill` 和业务库 `douyin_research`。浏览器入口经测试域名的反向代理 HTTPS 到 Windmill；Worker 仅使用 Docker 私有网络的 `WINDMILL_INTERNAL_URL`。不得将数据库端口或 Windmill 明文端口直接对公网发布。

```text
测试浏览器 ── HTTPS/FQDN ── 反向代理 ── Windmill Server
                                             │
                                      私有 Docker 网络
                                      ┌──────┴──────┐
                                 Windmill Worker  PostgreSQL
                                                   ├─ windmill
                                                   └─ douyin_research
```

外部接口全部由受控后端 runnable 发起：TikHub 用于研究数据，Ark 用于经人工审核后的 L3，ASR 用于已复核媒体。浏览器和 React App 不持有第三方密钥；MCP 不通过 URL query 传 token。详见 [DEPLOYMENT_V1.md](DEPLOYMENT_V1.md)、[PROVIDER_INTERFACE_V1.md](PROVIDER_INTERFACE_V1.md) 与 [WINDMILL_L3_REVIEW_WORKFLOW_V1.md](WINDMILL_L3_REVIEW_WORKFLOW_V1.md)。

## 3. 部署顺序与验收门

每一门失败都应停止进入下一门；修复后从失败门重新验收。不要为了通过检查临时降低 ACL、关闭 TLS 或把 Secret 写入环境文件。

### 门 A：主机、域名与镜像

输入：已确认的测试 FQDN、DNS、反向代理/证书方式、服务器访问权限和固定镜像 digest。

操作：

1. 在提交候选进入测试服务器前，先在本机或 CI 执行一次不读取部署配置、不调用 Provider 的完整 Overlay 烟测：

   ```bash
   TEST_SERVER_STACK_SMOKE=YES scripts/test-server-stack-smoke.sh
   ```

   它使用随机密码、一次性自签 localhost 证书和唯一 Compose project，真实启动 PostgreSQL、Windmill server、default/native worker 与 proxy，验证回环 TLS、明文拒绝、数据库/Server 无宿主端口、proxy 权限收敛和日志哨兵脱敏，并在退出时删除该项目、卷和临时目录。该结果只证明部署拓扑可以本机启动，不能替代正式 FQDN、CA 证书或真实身份验收。
2. 在候选主机运行 `scripts/test-server-preflight.sh --domain <测试 FQDN>`；它只读检查 Linux、Docker/Compose、磁盘、80/443 端口、DNS 与 HTTPS 出口，不安装或部署任何内容。DNS 尚在变更窗口时只能显式使用 `--allow-pending-dns`，并在启动前重新执行严格检查。
3. 在测试服务器取得目标分支的已审阅 commit，校验工作树无未审阅本地改动。
4. 将 `.env.test-server.example` 复制为 Git 忽略的 `.env.test-server`，替换每个 `CHANGE_ME` 与 `example.invalid`，设为 `0600`；为 PostgreSQL 与研究库设置独立随机 URL-safe 密码，并把证书目录设为仅目标主机可读的绝对路径。
5. 先执行配置渲染，不启动容器：

   ```bash
   docker compose -p douyin-research-test \
     --env-file .env.test-server \
     -f docker-compose.yml -f docker-compose.test-server.yml config >/dev/null
   ```

   `WINDMILL_BASE_URL` 必须是最终 `https://<测试 FQDN>`；`WINDMILL_INTERNAL_URL` 保持 Docker 内部地址，不能设成浏览器域名或 localhost。
6. 默认的 `docker-compose.test-server.yml` 用 `!reset []` 移除 PostgreSQL 和 Windmill 的直接端口，只由固定 digest 的 Nginx 暴露 80/443。证书目录只读挂载；代理 access log 仅记录无 query 的 `$uri`，不记录 Cookie、Authorization、请求体或请求头。
7. 经人工复核渲染结果后再启动固定 digest 的 PostgreSQL、Windmill server、default worker、native worker 与 proxy，确认健康检查和两个数据库边界。

输出与验收：浏览器只能经 `https://<测试 FQDN>` 访问；HTTP 明文入口重定向或拒绝、容器内部服务端口不公开、证书链与主机名匹配。记录实际 FQDN、证书颁发者、镜像 digest 与健康检查结果，但不记录私钥或密码。

回滚：停止这次 Compose 项目并恢复到部署前已确认的镜像/配置版本；若尚未导入数据，只删除本次创建的测试专用资源。若已经写入测试数据，先保留备份和事件记录，不做未经确认的卷删除。

### 外部反代变体：本机不运行 Nginx/TLS

当 HTTPS、证书和公网入口由**另一台专用反代机器**负责时，使用
`docker-compose.test-server-external-proxy.yml`，不要叠加默认的
`docker-compose.test-server.yml`。该 Overlay 不创建 Nginx 或 TLS 容器；它移除
PostgreSQL 所有宿主机端口，仅将 Windmill 固定发布到测试服务器的 RFC1918 私网
IPv4 地址 `:8000`。它没有 `0.0.0.0` 或公网地址默认值，配置文件缺失绑定地址会
在 Compose 渲染前失败。

输入与责任边界：网络负责人提供测试服务器私网 IPv4、专用反代机器的私网源 IP
和公开 FQDN；反代机器负责 HTTPS/TLS、证书更新和仅把流量转发到
`<测试服务器私网IP>:8000`。测试服务器防火墙只允许该反代源 IP 访问 TCP 8000；
不开放 80、443、5432，Docker 也不得映射 PostgreSQL。

在复制受 Git 忽略的环境文件并替换所有占位值后，先以 `0600` 权限验证，再渲染和
启动。以下命令不读取或显示 Secret 值：

```bash
cp .env.test-server-external-proxy.example .env.test-server-external-proxy
chmod 600 .env.test-server-external-proxy
scripts/test-server-external-proxy-validate.sh \
  --env-file .env.test-server-external-proxy \
  --project douyin-research-test

docker compose -p douyin-research-test \
  --env-file .env.test-server-external-proxy \
  -f docker-compose.yml \
  -f docker-compose.test-server-external-proxy.yml \
  up -d postgres windmill_server windmill_worker windmill_worker_native
```

后续执行 `scripts/test-server-release.sh` 的备份、迁移、验证或恢复演练时，追加
`--compose-overlay docker-compose.test-server-external-proxy.yml`；脚本只接受这两个
仓库内已审阅的 Overlay 文件名，并会先重复执行外部反代配置校验。

外部反代 profile 的完整数据库操作写法如下；`<backup-dir>` 只能使用同一脚本刚刚
创建并验证过的归档。恢复演练仍只使用固定临时库，绝不覆盖当前测试库：

```bash
scripts/test-server-release.sh backup \
  --env-file .env.test-server-external-proxy --project douyin-research-test \
  --backup-root /srv/douyin-research-test/backups \
  --compose-overlay docker-compose.test-server-external-proxy.yml

TEST_SERVER_RESEARCH_MIGRATE=YES scripts/test-server-release.sh migrate \
  --env-file .env.test-server-external-proxy --project douyin-research-test \
  --backup-root /srv/douyin-research-test/backups \
  --compose-overlay docker-compose.test-server-external-proxy.yml

scripts/test-server-release.sh verify \
  --env-file .env.test-server-external-proxy --project douyin-research-test \
  --backup-root /srv/douyin-research-test/backups \
  --compose-overlay docker-compose.test-server-external-proxy.yml

TEST_SERVER_RESTORE_DRILL=YES scripts/test-server-release.sh restore-drill \
  --env-file .env.test-server-external-proxy --project douyin-research-test \
  --backup-root /srv/douyin-research-test/backups \
  --compose-overlay docker-compose.test-server-external-proxy.yml \
  <backup-dir>
```

验收时从反代机器验证 `/api/version` 的 HTTPS 路径，再在测试服务器确认只有
Windmill 的 `私网IP:8000` 绑定、PostgreSQL 无宿主机绑定。`WINDMILL_BASE_URL`
必须保持最终公开 `https://<FQDN>`，以保证浏览器跳转和回调 URL 正确；它不是
测试服务器监听地址。现有含 Nginx 的 Overlay 仍保留给需要同机 TLS 终止的环境。
仓库 CI 还会在 Linux runner 选择实际 RFC1918 地址、生成随机数据库密码并启动
PostgreSQL、Windmill server/default/native worker；它以 Docker inspect 验证无 proxy、
PostgreSQL 无宿主机端口以及 Windmill 唯一准确的 `私网IP:8000` 绑定，退出时删除
该一次性 project 与卷。该烟测不配置 Provider，也不能替代反代源 IP 防火墙和真实
HTTPS 路径验收。

### 门 B：数据库、迁移与 Windmill 同步

输入：门 A 通过、部署前双库备份和对应 SHA-256 清单。

操作：

1. 先创建测试服务器专用、当前操作账号拥有且权限严格为 `0700` 的备份根目录；不得使用 `/`、仓库根或共享可写目录：

   ```bash
   sudo install -d -m 0700 -o "$(id -un)" -g "$(id -gn)" \
     /srv/douyin-research-test/backups
   scripts/test-server-release.sh backup \
     --env-file .env.test-server \
     --project douyin-research-test \
     --backup-root /srv/douyin-research-test/backups

   scripts/test-server-backup-verify.py \
     --backup-root /srv/douyin-research-test/backups \
     --backup-dir <上述命令输出的备份目录>
   ```

   脚本不会 `source` env；env 必须严格为 `0600` 且数据库标识键只能各出现一次。备份目录通过锁、临时目录、四份 artifact SHA-256、`test-server-backup-v1` 严格校验和原子发布完成；输出的 `globals.sql`、`globals.inventory`、`windmill.dump`、`research.dump`、`SHA256SUMS` 及 `manifest.txt` 必须作为一个整体保留。独立 verifier 的成功摘要提供 format、manifest/inventory 指纹和归档创建/验证 UTC 时间，可直接绑定脱敏证据包，不输出归档路径或内容。`globals.inventory` 只包含十六进制编码的角色名、非秘密角色属性及完整 membership 语义，不含密码散列或角色配置。
2. 复核上述备份路径后，以单次显式确认开关执行迁移，再独立验证 ledger、owner、权限和关键对象：

   ```bash
   TEST_SERVER_RESEARCH_MIGRATE=YES scripts/test-server-release.sh migrate \
     --env-file .env.test-server \
     --project douyin-research-test \
     --backup-root /srv/douyin-research-test/backups

   scripts/test-server-release.sh verify \
     --env-file .env.test-server \
     --project douyin-research-test \
     --backup-root /srv/douyin-research-test/backups
   ```

   迁移由研究数据库专用账号在单事务执行；已记录 migration 必须是仓库文件名/SHA-256 的连续合法前缀，完成后必须与仓库完整集合一致。任何历史 migration 改写、缺失中间版本、未知记录或数据前置约束失败都会在继续前关闭。
3. 按已审阅 commit 通过 Windmill CLI 同步 scripts、flows、App 和 lock；先比较同步计划，拒绝意外删除或未审阅覆盖。同步后由管理员生成**只含** `path` 与 `is_secret` 的 `0600` 变量元数据文件，再运行只读对象预检；不得使用可能返回变量值的 CLI 列表：

   ```bash
   scripts/test-server-windmill-preflight.sh \
     --workspace test-research \
     --profile /srv/douyin-research-test/wmill-profile \
     --app f/content_research/research_dashboard \
     --script f/content_research/analysis/manual_l3_preview \
     --resource f/content_research/research_db \
     --secret-variable f/content_research/research_db_password \
     --secret-variable f/content_research/research_action_writers \
     --nonsecret-variable f/content_research/l3_privacy_reviewers \
     --nonsecret-variable f/content_research/l3_budget_preview_config \
     --variable-metadata /srv/douyin-research-test/variable-metadata.json
   ```

   元数据文件契约仅允许 `{"variables":[{"path":"...","is_secret":true|false}]}`；脚本只调用 `wmill app|script|resource list --json`，不会读取值、get、sync 或 push。
4. 在浏览器确认 App 可加载，后端 runnable 可完成无 Provider 的读取/预览路径。

输出与验收：迁移记录、备份校验、Windmill sync 结果、App 版本和基础读路径成功。业务库与 Windmill 内部库不可混用；失败时不继续导入 Provider Secret。

回滚：停止后续 sync/任务，使用同一部署前备份只恢复到测试库或精确指定的恢复库；确认行数、关键约束、owner 和应用读路径，再决定是否切回。不得把测试恢复文件覆盖生产数据库。

### 门 C：真实身份、Folder ACL 与研究台写操作

输入：门 B 通过；admin、reviewer、viewer 各一个真实测试账号；Folder 分配、reviewer allowlist 和登录/IdP 方案已由身份负责人确认。

操作：

1. 通过 Windmill 正常的用户/组与 Folder UI 配置权限，**不直接写 Windmill 数据库**。
2. admin：验证管理与所需研究工作区可见性；reviewer：验证被授予的审核/研究 Folder；viewer：验证只读研究访问。
3. 对每个账号分别新登录浏览器，检查 Folder、App、Secrets/Resources、脚本运行入口与 Job 历史的可见性。
4. 在 reviewer/admin 身份验证 L3 审核候选、`WM_END_USER_EMAIL` 缺失失败关闭、非 allowlist 无法写审批，以及正文不进入 Job 输入、输出和日志。
5. 在允许写入的研究身份验证监测、专题、收藏与保存筛选：监测为团队共享且记录 actor；专题、收藏、筛选按 actor 隔离。Viewer 必须没有写后端 runnable 的执行权限。

输出与验收：每个测试账号的预期允许/拒绝矩阵、一次脱敏操作审计样本和浏览器刷新后的持久化结果。Folder ACL 是服务端边界，应用 allowlist 只是第二道防线；两者任何一个失败都不得进入付费 Provider 门。

回滚：撤销本次新增 Folder/组/用户授权与测试数据，保留审计事件。若权限有越权迹象，立即禁用相关 Folder runnable 和测试域名写入口，轮换可能暴露的 Windmill 会话/Secret，再复验。

### 门 D：Secret、网络出口与 Provider 小流量

输入：门 C 通过；Secret 通过 Windmill Secret/Resource 管理；网络负责人确认允许的目标域名；Provider 负责人确认测试额度、价格版本和一次调用上限。

Secret 最小集与用途如下；名称是接口契约，不是实际值：

| Provider | Secret/配置边界 | 测试动作 | 明确禁止 |
| --- | --- | --- |
| TikHub | TikHub API Key 仅在后端 runnable Secret 中 | 单个低量受控黄金路径；零重试、项目既有条数/未缓存调用/金额上限 | 浏览器持 key、共享 CI 带 key、批量历史回填 |
| Ark | `VOLCENGINE_ARK_API_KEY` 与经复核的 endpoint、模型回显、定价版本 | 一次严格结构化输出 smoke，固定 origin、禁止重定向、零重试 | 从页面选择模型/价格、自动重试、未审 L3 证据调用 |
| Doubao ASR | `VOLCENGINE_ASR_API_KEY` 与经复核的无 query 媒体交付信息 | 一次公开/已审媒体提交，至多项目定义的 poll 上限 | 上传个人媒体、输出转写正文、使用未审签名 URL |

操作：

1. 先从服务器做不含 Secret 的 DNS/TLS/HTTPS 连通性检查；失败时只交给网络负责人处理，不绕过代理或改用个人网络。
2. 将 Secret 写入 Windmill Secret/Resource，确认 UI、Job 输出、日志、`wmill` diff 和环境诊断均不可读到值；前端不接收 Provider key。
3. 为每项调用设置当天额度、调用数和单次范围；先运行 dry-run/contract/readiness，再由两人复核后执行一次小流量 live smoke。
4. 读取内部调用账本和 Provider 控制台聚合账单。若账单尚未出具，标记“待对账”，不能把估算值当作实际成本。

输出与验收：每个 Provider 仅一条脱敏执行记录（时间、调用数、上限、成功/失败、成本状态）；响应正文、媒体 URL、任务 ID、endpoint ID、请求 ID 和 Secret 不进入证据。任一预算、响应绑定、隐私审核、网络域名、账单或日志脱敏检查失败即停止该 Provider，且不重试。

回滚：立即禁用对应 runnable/flow schedule，撤销或轮换该测试 Secret，并保留最小调用账本。Provider 清退或保留要求发生变化时，按 [DATA_PROVENANCE_RETENTION_V1.md](DATA_PROVENANCE_RETENTION_V1.md) 先 dry-run，再经确认执行可追溯清理。

### 门 E：计划任务、并发、备份恢复与告警

输入：门 D 的 Provider 结果已对账或明确标记待对账；备份异机落点、保留策略、告警接收方已确认。

操作：

1. 首先按 [TEST_SERVER_MONITORING_V1.md](TEST_SERVER_MONITORING_V1.md) 生成并校验本地无外发监控契约，再用不外呼或 dry-run flow 验证 schedule 的时区、启停、失败状态、重复触发和手工停用；外呼 schedule 保持关闭，直到有单独的测试窗口和预算确认。本地契约的 `disabled_no_external_delivery` 只能证明调度边界，不能证明真实告警已投递或关闭。
2. 以测试数据验证同一 Provider/同一实体的并发与幂等行为；记录排队、拒绝或预算阻断，而不是通过增加无界 worker 规避。
3. 在暂停写入的验收窗口再次运行门 B 的 `backup`，把输出的**迁移后**备份目录记录为 `<backup-dir>`，生成/核验完整性清单，再复制到已确认的异机加密落点。
4. 在同一测试 PostgreSQL 实例执行数据库级恢复演练：

   ```bash
   TEST_SERVER_RESTORE_DRILL=YES scripts/test-server-release.sh restore-drill \
     --env-file .env.test-server \
     --project douyin-research-test \
     --backup-root /srv/douyin-research-test/backups \
     <backup-dir>
   ```

   脚本只创建固定临时库 `test_server_research_restore` 与 `test_server_windmill_restore`；其中任一名称已存在时立即拒绝，绝不删除或复用。成功后比较双库哨兵表行数、关键对象和 owner，并只清理本次创建的两个临时库。成功摘要只输出归档格式、manifest/inventory SHA-256、归档/完成 UTC 时间和实测秒数，不输出路径或内容。`globals.sql` 在这里仅验证 SHA-256，**不会**在共享实例恢复角色。
5. 使用与测试服务器相同的固定 PostgreSQL digest，在无网络、无端口、无宿主 bind mount 的一次性集群完成 globals/roles 演练：

   ```bash
   TEST_SERVER_GLOBALS_RESTORE_DRILL=YES scripts/test-server-globals-restore-drill.sh \
     --backup-root /srv/douyin-research-test/backups \
     --postgres-image <postgres镜像@sha256> \
     <backup-dir>
   ```

   脚本拒绝 tablespace，恢复失败即失败，并比较不含秘密的角色及完整 membership inventory；容器、卷或运行目录任一清理失败也不得宣称通过。将两个恢复摘要中相同的 manifest/inventory 指纹和各自实测耗时写入证据包。
6. 比较恢复库的 schema、约束与研究台只读路径，记录数据库恢复耗时及端到端实际 RTO。
7. 触发一次无敏感内容的任务失败/阈值告警，验证外部告警路由、值班接收和关闭记录；不要将真实 Secret 或正文作为告警探针。本地监控契约的 seal 不能代替这项外部证据。

输出与验收：计划任务状态、并发策略、备份清单、异机可取回证据、恢复验证、实际 RTO、已知 RPO、告警闭环。测试服务器只能声明其测得的值，不得将本机的 429 基线或恢复耗时外推为生产容量/SLO。

回滚：停用新 schedule、暂停相关 worker 或 Folder runnable、恢复最后一个经过校验的测试备份；若恢复失败，保留原库只读并升级给基础设施负责人，不重复覆盖源库。

## 4. 最终测试服务器验收清单

全部项目为“通过”才可以声称测试服务器 V1 已验收；任何“未执行/待对账”都应如实保留，不用本机证据补齐。

- [ ] 已记录测试部署 commit、固定镜像 digest、版本和变更审批。
- [ ] 测试 FQDN 的 DNS、正式 CA/TLS、HTTPS-only、反向代理日志脱敏和受限端口通过。
- [ ] `windmill` 与 `douyin_research` 分库、专用业务角色和迁移后权限通过。
- [ ] `wmill sync push` 的内容、lock 与 App 基础读路径通过。
- [ ] 三个真实账号完成登录、Folder UI、Secret/Resource 不可见性和 Viewer 写入拒绝验证。
- [ ] L3 身份、正文隔离、指纹审批、预算预览与失败关闭通过。
- [ ] 研究台共享监测与 actor 私有专题/收藏/筛选的读写边界通过。
- [ ] 每个获准 Provider 完成一次小流量 smoke，账本、预算和成本状态已记录。
- [ ] schedule/并发的安全停用与失败处理通过；未授权外呼 schedule 保持禁用。
- [ ] 同一 `test-server-backup-v1` 归档已校验并存放于异机受控位置；双库恢复与一次性 globals/roles 恢复的 manifest/inventory 指纹绑定、清理和实测耗时通过。
- [ ] 监控/告警已至少触发一次并完成关闭；日志与证据中无 Secret、正文或 bearer 数据。
- [ ] `test-server-evidence-v1` 通过 `--require-complete` 校验并生成独立 SHA-256；原始证据位于受控位置且未写入 Git。

## 5. 进入生产前的独立门

测试服务器通过不授权生产。生产前需重新指定生产 FQDN/证书、生产身份与最小权限、生产 Secret 和轮换、数据保留/清退策略、预算/采购、备份 RPO/RTO、监控值班、变更窗口和回滚责任人，并以生产环境的实际证据重新执行相应验收。

本机已完成的证据见 [LOCAL_V1_ACCEPTANCE_2026-09-20.md](LOCAL_V1_ACCEPTANCE_2026-09-20.md)；本机安全演练的明确范围见 [LOCAL_SECURITY_ACCEPTANCE_V1.md](LOCAL_SECURITY_ACCEPTANCE_V1.md)。两者均不构成测试服务器或生产上线证明。
