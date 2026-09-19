# V0 TikHub Test Harness 设计

日期：2026-09-19

## 目的

没有真实 API Key 前先把测试方式定死；拿到 Key 后只需配置 Windmill Secret 即可执行。

## 测试原则

每个 endpoint 只做最小调用，不做批量扫库。

每次调用保存：
- endpoint key
- SDK method
- normalized request
- raw response
- HTTP/provider code
- latency
- pagination fields
- estimated/actual cost
- SDK version
- tested_at

## V0 Cases

### T01 Billboard

验证：
- 低粉爆款
- 高完播
- 高涨粉
- 高点赞
- 上升热点/搜索/话题

检查：
- aweme_id / sec_uid 稳定字段
- metrics
- rank
- time window
- tag/category
- pagination

### T02 Structured Search

验证：
- video/content query
- user search
- follower band
- filter options

重点确认 search_id / cursor 行为。

### T03 Batch Video Detail

优先 App V3 `fetch_multi_video_v2`。

用 1、2、50 个 ID 分别测试。

检查：
- 请求体格式
- 每条视频状态
- 删除/私密/版权状态
- plays/likes/comments/shares/collect
- author id
- 成本是否固定单次计费

### T04 Account Posts

App V3 `fetch_user_post_videos`：
- sort_type=0 latest
- sort_type=1 hottest
- cursor
- count <=20
- normal/lite channel fallback

### T05 Comments

- 默认 count
- 第一页
- cursor
- 热评排序实际行为
- comment id
- like count
- replies marker

不做全量翻页。

### T06 Comment Word Cloud

确认普通候选视频是否都能返回。
若覆盖不足，仅作为 Billboard 深研补充，不进入主链。

### T07 Cost / Usage

验证：
- endpoint price
- calculate price
- daily usage
- 本地 external_api_call 对账

### T08 Failure

最小验证：
- invalid video id
- invalid account id
- 429（若可安全触发则测试，否则读取 SDK 行为）
- 4xx/5xx retry policy
- timeout

## 输出

测试完成生成：
- `docs/tikhub/V0_ENDPOINT_MATRIX.md`
- `docs/tikhub/samples/*.json`（脱敏）
- Provider mapping
- price/call class
- recommended cache TTL
- recommended max pages
