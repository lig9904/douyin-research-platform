# 媒体 Worker 部署与验收

## 实现与部署状态

入口为 `f/content_research/collectors/ingest_video_media`，依赖固定到包含单视频详情来源兼容性的提交 `9d638f625846766361bb2106b709e5db17f9b9eb`。仅接受内部视频 UUID；数据库固定从 `f/content_research/research_db` 资源读取，不接受调用者数据库、任意下载 URL、凭据、actor 或审核批准。脚本不发起付费详情刷新、ASR、LLM 调用，不更改桶权限。已持久化的业务失败会转成安全异常，使 Windmill 也标记失败，不以正常返回伪装任务成功。

2026-09-21 已通过浏览器 JumpServer 在测试服务器构建并部署媒体镜像 `douyin-research-media-worker:87eeac7`，两个普通 Worker 均已确认 ffmpeg/ffprobe、Python 3.13.5 及可写临时目录；server/native/postgres 未替换。镜像 ID 为 `sha256:08e0267834a4f76ef1eb33f0fe6a790fe5eddd7dff926e5b0f0712974d549da5`。无网络临时容器实际转换合成音频并验证 pcm_s16le、16000 Hz、单声道通过，不是真实视频链路验收。

主机临时目录 `/srv/douyin-research-test/media-tmp` 绑定到容器 `/srv/research-media-tmp`，findmnt 确认位于 `/dev/sdb1` 的 500G 数据盘。迁移账本已验证至017；迁移前备份 `/srv/douyin-research-test/backups/20260920T190539Z`。备份校验不等于恢复演练通过。

上述是 2026-09-21 的历史部署状态。2026-09-26 测试服回查时，媒体脚本仍固定旧服务提交 `fd2694729c784a0f2e34b2c0a4d6746da57813fe`；随后只更新这一脚本的固定提交并发布，实测结果见下。`media_storage_config`（Secret）与 `automation_worker_identity` 已存在，部署及核验过程未读取其值。普通 Worker 的系统 `python3` 为 3.13.5；实际 Windmill 作业日志显示 Python 3.14 执行，不能将系统默认解释器与脚本运行解释器混为一谈。

### 2026-09-26 测试服实测

- 单脚本发布版本 `83e07844f3d7170a`；Windmill 发布页展示非空锁，首行 `# py: 3.14`，含 `psycopg==3.3.6`、`psycopg-binary==3.3.6` 与上述核心提交。未同步整个工作区，未新建 CLI 令牌。
- 对九九项目已接受的 HandsMini《师兄去哪了》执行内部视频 UUID `67beaca8-7b5a-41dd-ac9b-5bbe83692176`：Windmill 作业 [`01a0dd80-5b60-8992-a237-1a3f584171a6`](https://dy.yudao.cc:6443/run/01a0dd80-5b60-8992-a237-1a3f584171a6?workspace=test-research) 成功，约 8.9 秒；结果 `reused=false`、`external_paid_calls=0`、流水线 `fec656f3-19ca-4440-95c4-6872d107f7ea` 为 `success`。实际作业日志显示 `Python (3.14)`。
- 测试库形成且仅形成两条 `media_asset`：MP4 `a866c64d-7f55-44c9-86de-405594208736`（4,844,398 字节，SHA-256 前缀 `3eddeb...`）和 WAV `8e52cd4c-b382-4be5-9906-01eed0e90390`（641,366 字节，SHA-256 前缀 `6ca0b1...`）；后者指向前者作为父资产。两条都有缓存详情来源。研究台该项目视频库可加载“待审核 WAV · 626.3 KB”，这是页面级读取入口的验证，尚不等于用户已完整试听或对象丢失恢复演练。
- 原参数再次运行，Windmill 作业 [`01a0dd82-01f9-1b80-3b11-d119be5c8861`](https://dy.yudao.cc:6443/run/01a0dd82-01f9-1b80-3b11-d119be5c8861?workspace=test-research) 成功，约 0.8 秒；返回 `reused=true`、相同两个 asset_id、`external_paid_calls=0`。流水线 `9edb3cda-22fb-428c-8326-231f5c5b83e2` 为 `success`，测试库仍只有两条资产记录。重复作业日志未显示重新下载，但尚未通过 CDN 出站观测独立证明零网络传输。
- 对同一 HandsMini 账号、项目已接受的《奥术之贼｜01 原创西幻cg动画》（平台 ID `7686489876787040677`）做第二例时，两次人工输入了错误的内部 UUID：[`01a0dd88-8b5f-bd72-d91e-28acba259deb`](https://dy.yudao.cc:6443/run/01a0dd88-8b5f-bd72-d91e-28acba259deb?workspace=test-research) 与 [`01a0dd8a-4be0-776b-c348-71aff0b44782`](https://dy.yudao.cc:6443/run/01a0dd8a-4be0-776b-c348-71aff0b44782?workspace=test-research) 均在视频存在性预检处拒绝，未开始媒体处理。按平台 ID 重新查询数据库得到正确内部 UUID `9a6ff410-3be9-474c-af15-80b2309e222b`；对应作业 [`01a0dd8a-f1bf-35e7-5078-408522e61ff3`](https://dy.yudao.cc:6443/run/01a0dd8a-f1bf-35e7-5078-408522e61ff3?workspace=test-research) 则在 `media_download_failed` 阶段失败。测试库流水线 `d71c01f6-0501-4412-9ee5-bfb9fbc4fe70` 为 `failed`、`external_paid_calls=0`，没有生成该视频的媒体资产。所选 TikHub 单视频详情缓存时间为 2026-09-26 08:49 UTC（北京时间 16:49），失败发生在北京时间约 19:47；缓存 URL 过期是待核假设，不能仅据约 3 小时时差确认 CDN HTTP 状态。应先精确刷新该视频的详情，再重试媒体入库，并保留这次失败审计。
- 随后在九九项目创建仅含该平台 ID 的单次研究任务（任务名最初误写“奥术之城”，页面核对原题后改为“媒体源刷新｜奥术之贼第1集”）。只读预检确认当时仅此一条到期任务；手动调用 [`dispatch_due_research_briefs` 作业 `01a0dd91-079d-55a2-671b-ec834991494a`](https://dy.yudao.cc:6443/run/01a0dd91-079d-55a2-671b-ec834991494a?workspace=test-research)，回显 `due_count=1`、`dispatched_count=1`。项目任务运行 `48ecdf09-8f34-4527-a0bb-de25e798bd8d` 为 `success`，单条详情实际调用 1 次、缓存命中 0 次、SDK 重试 0 次、无 ASR/L3；`external_api_response.id=125` 于 11:54:27 UTC 保存目标 ID。调用账本 `douyin.app.one_video` 本地报价 USD 0.001，`actual_cost` 仍为空，应以 TikHub 日账对账，不把报价当实扣。一次性任务之后为 `paused`、`next_due_at=null`，不会继续自动付费刷新。
- 新响应入库后立刻重跑媒体作业 [`01a0dd91-f12f-1f8a-911a-58f764c16a86`](https://dy.yudao.cc:6443/run/01a0dd91-f12f-1f8a-911a-58f764c16a86?workspace=test-research)：约 13.5 秒成功，`reused=false`、`external_paid_calls=0`；流水线 `04b761b8-8e77-4abb-bd9e-1e751e24153d` 的数据库状态与条目均为 `success`、`api_cost=0`。数据库恰有 MP4 `9a6cf652-ff14-46c5-885b-20565fcb260f`（4,252,327 字节、SHA-256 前缀 `d8852df5979f`）及其子 WAV `581252f8-a7c5-453a-881c-40f29395733d`（9,122,408 字节、SHA-256 前缀 `d34fc4b2672d`），两者 `source_response_id=125`。研究台“九九 IP 号”项目的视频库可加载该条“待审核 WAV · 8908.6 KB”。这证明刷新后的完整媒体链路和页面音频读取入口跑通，但旧请求的实际 CDN 状态码未采集，仍不能断言其失败必由签名过期引起；该长音频也尚未由人完整核听或同意云端转写。
- 未做这条新音频的人工核听、云端 ASR 或 L3；这些仍需逐条审核后验收。两条新 AI 角色候选也仍仅有公开元数据，不应据此宣称九九业务指导意见已成立。

## 服务端配置

在受控工作区设置：

1. Secret 变量 `f/content_research/media_storage_config`，值为 JSON。
2. 服务端变量 `f/content_research/automation_worker_identity`，例如 `windmill:test-research:automation`。它是审计归属标签，不是绕过 Windmill 权限的认证凭据；调用权限仍由工作区和文件夹 ACL 决定。

JSON 结构如下，占位字段必须用实际核验的值替换。不要把实际 Key 放进 Git、终端参数或验收记录。

```json
{
  "endpoint": "http://192.168.200.20:9000",
  "public_endpoint": "https://minio.yudao.cc:6443",
  "bucket": "douyin-research-media",
  "region": "us-east-1",
  "access_key_id": "<existing scoped access key>",
  "secret_access_key": "<existing scoped secret key>",
  "force_path_style": true,
  "use_ssl": false,
  "storage_location": "research-media-v1",
  "temp_directory": "<verified absolute worker directory on the 500G disk>",
  "allowed_download_hosts": ["zjcdn.com", "amemv.com"],
  "ffmpeg_binary": "<verified executable path>",
  "max_download_bytes": 2147483648
}
```

CDN 域名来自测试服已缓存详情的实际媒体 origin，不根据任意页面输入扩展。下载会逐跳解析并固定经过检查的公网 IP，保持原 Host/TLS SNI 和证书验证。内网 S3 使用独立存储配置，不复用 CDN 下载白名单。

`max_download_bytes` 是单个 Worker 文件处理保护，可由可信配置调整，不是反代上传限制或金额上限。脚本元数据默认超时 900 秒、并发上限 2；同视频在数据库内再次串行去重。Community Edition 即使不执行全局并发限制，同视频数据库锁仍须生效。

## 部署顺序

CI 运行 `35525573965` 已实际构建该镜像并在无网络容器内完成音频转换：Python 3.13 检查通过，输出为 `pcm_s16le`、16000 Hz、单声道；ffmpeg 包版本 `7:7.1.5-0+deb13u1`。镜像继承上游默认用户（Config.User 为空），未增加 USER 覆盖。此证据仅证明 CI 镜像运行时，不证明测试服务器挂载、Windmill 作业或真实媒体链路。

可重复运行时由 `deploy/media-worker/Dockerfile` 与显式启用的 `docker-compose.media-worker.yml` 提供。它只替换普通 Worker 镜像，不修改服务器/native Worker，不新增 Nginx 或端口。基础 Windmill 镜像固定 digest；ffmpeg 来自基础镜像官方 apt 仓库，最终构建镜像应保存镜像 ID 与包版本（不是字节级可复现构建）。本机没有 Docker daemon，镜像构建已由上述 CI 验证；服务器部署检查仍不可省略。

上线前先确认上游容器用户与运行权限兼容，再构建验证 `ffmpeg -version`、`ffprobe -version`、Python 3.13 和实际音频转换。指定 `MEDIA_WORKER_IMAGE` 为版本化镜像标签，`MEDIA_TEMP_HOST_PATH` 为通过 `findmnt -T` 核验在 500G 盘上的现有目录；挂载不会自动创建不存在的主机目录。Secret 中 `temp_directory` 对应 `/srv/research-media-tmp`，`ffmpeg_binary` 对应 `/usr/bin/ffmpeg`。不得仅凭路径包含 `/srv` 就认定物理磁盘正确。

构建并验证后才在原 Compose 文件列表末尾追加媒体 overlay，保留既有双 Worker、数据库、缓存配置及项目名。部署前确认无正在执行的任务；只更新普通 Worker。回滚到原文件列表和原固定 Windmill 镜像，保留媒体目录和数据库，不删除已有对象。

1. 通过 JumpServer 只读确认镜像 OS、ffmpeg 可执行文件、容器存储映射和临时目录实际落在 500G 盘。不要在应用服务器部署 Nginx。
2. 准备可重复部署的 Worker 音频运行时；仅手工修改一次性容器不算完成。
3. 备份数据库并部署迁移 016；保留现有原始详情响应和视频数据。
4. 配置上述 Secret 和身份标签，核验桶保持私有、配置桶与真实上传桶一致。不要新建公开权限或替换用户密钥。
5. 只推送这一个脚本，使用测试服已配置的 CLI 工作区，不做全量覆盖：

```sh
wmill script push f/content_research/collectors/ingest_video_media.py --workspace test-server
```

6. 以一个实际 source_video UUID 执行。不得把平台 aweme_id 当作内部 UUID；找不到匹配视频或未部署媒体 schema 会在读取 Secret 前停止。
7. 通过后再接批次调度，避免为相同数据额外创建重复计划。

## 必须保存的真实验收证据

- 成功 run_id、video_id、视频/音频 asset_id 及内容 hash/size；记录版本和迁移，不输出 URL 或 Secret。
- Worker 确实从已缓存详情取得该视频并下载，内网 MinIO 上传成功；音频可实际解析为单声道 16 kHz PCM WAV。
- 同视频再次执行返回 reused，未再次访问 CDN；音频阶段失败后重跑从 S3 恢复视频，不重复付费查询。
- 缺失对象、读取被拒绝、内容类型/摘要不匹配均不能返回成功。
- UI 的短期签名播放、ASR 输入审核和真实转写仍是后续独立验收，不能由 Worker 成功代替。
- 普通异常自动清理本次临时目录；强制终止留下的目录需另做有边界的运维清理验证，不得遍历删除其他 Worker 的文件。
