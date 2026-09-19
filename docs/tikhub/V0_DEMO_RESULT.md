# TikHub V0 Demo Verification

日期：2026-09-19

## 已可无需 API Key 验证

TikHub 提供固定抖音作品 Demo：

`GET /api/v1/demo/douyin/web/fetch_one_video`

官方说明：
- 固定 aweme_id：7534641277405531446
- 1小时缓存
- 免费使用，不计费
- 用于测试连接、数据格式与开发验证

## 本轮在线验证

实际响应：
- HTTP 可访问
- TikHub envelope `code=200`
- 有 `request_id`
- router 为 demo Douyin Web endpoint
- `data.aweme_detail` 存在

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
- 校验 envelope
- 校验 canonical 最小字段
- raw response 写入 `tmp/tikhub-v0/`（已 gitignore）

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

## 下一步

有真实 Key 后只运行最小测试：
1. low-fan billboard
2. Creator travel material billboard
3. Search V2
4. App V3 one-ID batch

四条通过后，再扩大到 Creator signal → related videos、comments、cost API 等。

不在第一轮同时测全部接口。
