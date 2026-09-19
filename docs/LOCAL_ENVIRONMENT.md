# Local / Single-Server Environment

日期：2026-09-19

本文件对应 GitHub Issue #2。

## 目标

只启动 V1 最小基础设施：

- PostgreSQL 18
- Windmill Server 1.815.0
- 1 × default Worker
- 1 × native Worker
- `windmill` 数据库
- `douyin_research` 数据库

暂不启动：

- TikHub 正式采集
- Web 研究台
- MCP
- ASR
- OCR/media worker
- Redis / MQ
- NAS
- LibTV

## 1. 前置条件

Linux / macOS 开发机或 Linux 服务器：

- Docker Engine / Docker Desktop
- Docker Compose v2
- curl

建议至少：

- 2 CPU
- 4 GB RAM
- 20 GB 可用磁盘

这是 V0/V1 起步配置，不是最终容量承诺。

## 2. 创建环境文件

```bash
cp .env.example .env
```

生成两个不同的 URL-safe 密码：

```bash
openssl rand -hex 24
openssl rand -hex 24
```

分别填写：

- `POSTGRES_PASSWORD`
- `RESEARCH_DB_PASSWORD`

并同步修改：

- `WINDMILL_DATABASE_URL` 中的 postgres 密码

不要提交 `.env`。

## 3. 访问范围

默认：

```env
WINDMILL_BIND_HOST=127.0.0.1
```

因此 Windmill 只监听本机。

开发机直接访问：

```text
http://localhost:8000
```

如果部署到园区服务器：

优先让 nginx / Traefik / 现有反代通过 HTTPS 转发到 `127.0.0.1:8000`。

不要为了方便直接把：

```env
WINDMILL_BIND_HOST=0.0.0.0
```

暴露到公网。

## 4. 启动

```bash
docker compose pull
docker compose up -d
```

观察：

```bash
docker compose ps
docker compose logs -f windmill_server
```

Windmill 健康检查：

```bash
curl http://127.0.0.1:8000/api/version
```

官方 Windmill 本身也使用 `/api/version` 做容器健康检查。

## 5. 首次数据库初始化

PostgreSQL volume 第一次创建时会自动运行：

```text
db/init/01-bootstrap-research.sh
        ↓
create role douyin_research
        ↓
create database douyin_research
        ↓
db/schema.sql
```

因此首次成功启动后应同时存在：

- `windmill`
- `douyin_research`

业务 Schema 中的对象归 `RESEARCH_DB_USER` 所有，而不是 Windmill。

## 6. 验证

```bash
bash scripts/verify-stack.sh
```

检查：

- Compose 服务状态
- Windmill `/api/version`
- `douyin_research` 是否存在业务表
- `source_video` 是否创建成功

## 7. 停止

保留数据：

```bash
docker compose down
```

删除所有本地数据，重新初始化：

```bash
docker compose down -v
```

**警告：`-v` 会删除 PostgreSQL volume。**

## 8. Schema 变更

`docker-entrypoint-initdb.d` 只在空数据目录首次启动时运行。

因此以后修改 `db/schema.sql`：

- 开发期可在明确可丢数据时 `docker compose down -v` 重建；
- 进入有历史数据阶段后，必须使用 `db/migrations/`，不能靠删 volume。

## 9. 备份

```bash
bash scripts/backup.sh
```

默认生成：

```text
backups/<UTC timestamp>/
├── globals.sql
├── windmill.dump
├── douyin_research.dump
└── manifest.txt
```

包含：
- PostgreSQL roles/globals
- Windmill 数据库
- 研究业务数据库

后续生产环境再增加自动定时备份与 restore drill。

## 10. Windmill 初始化后的安全设置

第一次进入 Windmill 后：

1. 立即设置管理员密码。
2. 创建单一项目 workspace。
3. 暂时不配置 TikHub Key。
4. 不启用匿名 Public App。
5. 后续 MCP 启用时，在 Instance Settings 打开：
   `Disable token in MCP URLs`
6. 不挂载宿主 Docker socket。
7. 不启动 debugger/multiplayer。

## 11. 为什么只有一个普通 Worker

官方通用 compose 默认多个 worker，但我们的 V0 数据量极小。

先：

- 1 × default worker
- 1 × native worker

只有队列实际积压才加 replica。

## 12. 版本

当前 V0 基线：

- Windmill `1.815.0`
- PostgreSQL `18`

Windmill GitHub 在 2026-09-18 发布 v1.815.0；官方容器 tag 使用 `1.x.y` 形式。

生产版本不跟 `main` / floating `latest`。
