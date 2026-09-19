# TikHub V0 Demo Verification

日期：2026-09-19

## 已可无需 API Key 验证

TikHub 提供四个免费 Demo：

- `GET /api/v1/demo/douyin/app/fetch_one_video`
- `GET /api/v1/demo/douyin/web/fetch_one_video`
- `GET /api/v1/demo/douyin_search/app/general_search`
- `GET /api/v1/demo/demo/cache_status`

官方说明：
- 固定 aweme_id：7534641277405531446
- 1小时缓存
- 免费使用，不计费
- 用于测试连接、数据格式与开发验证

## 本轮在线验证

四个端点实际响应：
- HTTP 200
- TikHub envelope `code=200`
- 有 `request_id`
- `router` 与各自 Demo endpoint 一致

App/Web 固定作品：
- `data.aweme_detail` 存在
- 都存在 `statistics.play_count`

综合搜索：
- 顶层卡片 19 个
- `cursor=20`
- `has_more=1`
- 有 `backtrace`
- 卡片内可提取稳定 `aweme_id`

Cache status：
- 有 `total_cached_items`
- 每项有剩余缓存秒数与过期状态

已观察到可用于 canonical normalize 的字段：
- `aweme_id`
- `author.sec_uid`
- `author.uid`
- `author.nickname`
- `author.follower_count`
- `duration`
- `desc`
- `caption`
- `create_time`
- `item_title`
- `video`/媒体相关结构
- cover / avatar 等 URL

说明：
- TikHub 响应 envelope 与业务 data 必须分层解析
- 同一返回中包含大量抖音原始字段，不能原样映射成业务 Schema
- raw payload 应保留，canonical table 只提最小稳定字段

## 自动测试脚本

`scripts/tikhub/v0_smoke.py`

默认：
```bash
python scripts/tikhub/v0_smoke.py demo
```

特点：
- 不需要 API Key
- 不计费
- 一次验证四个免费端点
- 校验 envelope、视频 canonical 最小字段、搜索分页/ID 和缓存状态结构
- raw response 写入 `tmp/tikhub-v0/`（已 gitignore）

输出到终端的摘要不包含完整 `request_id`、作者 ID 或原始搜索结果；这些只保存在本地 gitignored raw 文件中。

## 结论边界

免费固定样本已经证明 App/Web 返回结构中存在播放量字段，但不能据此认定所有普通账号作品都稳定返回播放量。普通小号、中腰部账号、星图达人、删除/私密作品的覆盖率与准确性仍需正式 Key 交叉实测。

## 付费 Smoke 安全闸门

所有正式接口默认禁止运行。

必须同时：

```bash
export TIKHUB_API_KEY="..."
export TIKHUB_ENABLE_PAID_SMOKE=YES
```

再明确选择：
- billboard
- creator
- search
- appv3

`all-paid` 也硬限制为最多4次平台调用且不翻页。

## 已完成的正式 Key 验证

正式 Key 的分批结果与证据边界统一记录在 `docs/V0_API_TEST_MATRIX.md`。本机于
2026-09-20 又以零重试方式单独复验一次 low-fan billboard；未把 Secret、原始响应或
私有标识写入仓库。后续扩大样本仍必须走独立的调用上限、预算和结果审查，不因首轮端点
可用就自动开启持续采集。
