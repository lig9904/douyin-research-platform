# 私有 S3 媒体存储

## 当前边界

已实现存储适配器和独立真实验收脚本；尚未接入研究台下载队列、数据库媒体记录或云端 ASR 自动链路。不得把单元测试通过标记为已部署或已完成全链路。

`S3MediaStorageConfig` 显式接收 endpoint、bucket、region、access_key_id、secret_access_key、force_path_style、use_ssl，以及可选的 HTTPS public_endpoint。适配器本身不读取环境变量或 Windmill Secret，后续入口负责从受控配置装配。凭据不写源码、Git、日志或验收报告。

- 上传端点使用应用服务器可访问的内网 S3 origin。
- 公网 HTTPS origin 专门用于预签名下载。签名时就使用外网域名和端口，不能在签名完成后替换域名。
- 桶保持私有。上传不设置 ACL，不修改桶权限、不创建密钥、不创建桶。
- 对象键按 SHA-256 内容寻址；HEAD 检查摘要 metadata 和大小，已存在且一致时复用；403 不当作不存在。
- SDK 使用 S3v4，连接超时 5 秒、读取超时 30 秒，总尝试次数 1。
- 签名有效期 60–3600 秒，默认 900 秒。签名链接属于临时凭据，不记录完整 URL。
- Nginx 保留 `$http_host`（包括非默认端口），不重写对象路径；S3 和管理控制台不共用此反代入口。

## 小规模真实验收

在安装项目依赖后运行：

```sh
PYTHONPATH=src .venv/bin/python scripts/s3_media_smoke.py \
  --endpoint https://YOUR-S3-HOST:6443 \
  --public-endpoint https://YOUR-S3-HOST:6443 \
  --bucket YOUR-PRIVATE-BUCKET
```

Access Key 和 Secret Key 通过终端无回显提示输入，不作为命令行参数、不保存。应用服务器验收时，将 endpoint 改为内网地址，public-endpoint 保持公网地址。

脚本生成 1644 字节、0.1 秒静音 WAV，验证上传、重复调用复用、5 分钟 S3v4 签名下载、下载字节和 SHA-256 一致、匿名 GET 403、篡改签名 GET 403。成功时保留一个极小验收对象，并打印其非敏感 key/size/hash；不删除已有对象，不调用任何付费接口。

失败报告只保留阶段和白名单错误码，不输出服务商异常正文或签名 URL。`InvalidAccessKeyId` 表示服务端不认可实际提交的 Access Key，需要核对原始文本与目标 MinIO 实例；不要直接放宽桶权限，也不要自动尝试多种猜测密钥。

实际通过上述验收后，才继续部署应用配置及媒体下载任务。静音 WAV 验收不证明真实视频提取或 ASR 转写已完成。
