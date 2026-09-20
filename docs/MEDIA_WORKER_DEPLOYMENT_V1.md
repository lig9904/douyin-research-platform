# 媒体 Worker 部署与验收

## 已实现与未部署

入口为 `f/content_research/collectors/ingest_video_media`，依赖固定到媒体服务已合并提交 `fd2694729c784a0f2e34b2c0a4d6746da57813fe`。仅接受内部视频 UUID；数据库固定从 `f/content_research/research_db` 资源读取，不接受调用者数据库、任意下载 URL、凭据、actor 或审核批准。脚本不发起付费详情刷新、ASR、LLM 调用，不更改桶权限。已持久化的业务失败会转成安全异常，使 Windmill 也标记失败，不以正常返回伪装任务成功。

当前仅完成代码及隔离测试。测试服务器现有 Worker 未检测到 ffmpeg；必须补齐可重复部署的音频运行时并完成下面的验收，不能把脚本存在当作运行成功。

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

可重复运行时草案为 `deploy/media-worker/Dockerfile` 与显式启用的 `docker-compose.media-worker.yml`。它只替换普通 Worker 镜像，不修改服务器/native Worker，不新增 Nginx 或端口。基础 Windmill 镜像固定 digest；ffmpeg 来自基础镜像官方 apt 仓库，最终构建镜像应保存镜像 ID 与包版本（不是字节级可复现构建）。本机没有 Docker daemon，此草案尚未实际构建验证，不得直接视为已可部署。

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
