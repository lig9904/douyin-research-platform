# V0 无 API Key 预验证方案

日期：2026-09-19

TikHub 官方提供免费 Demo endpoint，可以在正式 API Key 之前验证一部分工程结构。

## 可用 Demo

### Douyin APP fixed video

```text
GET https://api.tikhub.io/api/v1/demo/douyin/app/fetch_one_video
```

- 固定 aweme_id
- 1 小时缓存
- 免费
- 无需 Authorization

SDK 中已有：
```python
client.demo.douyin_app_fetch_one_video()
```

### Douyin Web fixed video

```text
GET /api/v1/demo/douyin/web/fetch_one_video
```

### Douyin Search fixed query

```text
GET /api/v1/demo/douyin_search/app/general_search
```

- 固定关键词“美食”
- 1 小时缓存
- 免费

### Cache status

```text
GET /api/v1/demo/demo/cache_status
```

## 拿 API Key 前可以先验证什么

1. Windmill worker 是否能访问 api.tikhub.io
2. HTTP timeout / JSON decode
3. TikHub response envelope
4. raw payload 保存
5. Provider normalizer 的基本结构
6. external_api_call 日志
7. provider error wrapper
8. test fixture 保存策略
9. Web 研究台是否能展示一个 Demo Case
10. read-only allowlist 测试

## 不能用 Demo 验证什么

- 真实 Billboard 字段
- Index 字段
- 分页
- 价格
- 429
- 真实账号作品
- batch 50
- 评论词云覆盖率
- App V3 normal/lite 差异
- 各类画像覆盖率
- 真实 freshness

这些仍属于 Issue #1 的 API Key 实测。

## 建议

V1 环境初始化后，第一条自动化集成测试就调用 Demo APP fixed video。

这样可以把：
Windmill → TikHub → Normalize → PostgreSQL → App
整条链先跑通，再接收费接口。
