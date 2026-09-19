# V0 TikHub Test Harness 设计

日期：2026-09-19

## 目的

没有真实 API Key 前先把测试方式定死；拿到 Key 后只需配置 Windmill Secret 即可执行。

## 测试原则

每个 endpoint 只做最小调用，不做批量扫库。

每次调用保存：
- endpoint key
- SDK method / REST path
- normalized request
- raw response
- HTTP status
- TikHub envelope code
- upstream/platform status code（如有）
- latency
- pagination fields
- estimated/actual cost
- SDK version
- tested_at

任何 HTTP 2xx 都必须再经过 TikHub response-envelope 校验，不能直接视为业务成功。

## V0 Cases

### T01 Billboard

验证：
- 低粉爆款
- 高完播
- 高涨粉
- 高点赞
- 上升热点
- 飙升搜索
- 飙升话题
- 同城热点

检查：
- aweme_id / sec_uid 稳定字段
- metrics
- rank
- time window
- tag/category
- pagination
- TikHub envelope code

### T02 Creator Center

验证：
- creator hot spot
- creator hot topic
- creator hot music
- creator material center
- config/category options

重点验证：
- 旅行 / 剧情 / 二次元 / 创意 / 文化教育等垂类
- 24h / 7d / 30d 或榜单类型参数
- query_id 是否稳定可复用

### T03 Creator Signal → Related Videos

使用 Creator 榜单返回的 query_id 调：
`/api/v1/douyin/creator/fetch_creator_material_center_related`

验证：
- related video list
- aweme_id
- pagination
- billboard_type
- 是否比关键词搜索关联更稳定

此 endpoint 当前在线文档存在，但 SDK 2.1.1 未发现生成方法，因此专门验证 REST fallback。

### T04 Structured Search

验证：
- video/content query
- user search
- follower band
- filter options

重点确认：
- search_id / cursor 行为
- 新 Search V1/V2 与旧 deprecated endpoint 的差异

### T05 Batch Video Detail

优先 App V3 `fetch_multi_video_v2`。

用 1、2、50 个 ID 分别测试。

检查：
- 请求体格式
- 每条视频状态
- likes/comments/shares/collect
- 是否稳定包含 play_count
- author id
- 成本是否固定单次计费

如果 App 批量详情为空：
- 用官方建议的 Web batch fallback 查看 filter_list.reason
- reason=5 → private
- reason=10 → partial visibility
- reason=8 → 只保留 raw reason / generic unavailable，不自行判断“删除”或“海外版权”

### T06 Batch Video Statistics

验证 `fetch_multi_video_statistics`：

重点：
- 单次最大 aweme_ids
- play_count 是否稳定
- like/download/share 字段
- 真实 endpoint price（通过 get_endpoint_info / calculate_price）
- 是否适合 50-ID batch
- 与普通 batch detail 的字段重叠
- 是否值得只在 L2/L3 使用

此项决定“播放量快照”的成本策略，必须单独验证。

### T07 Account Posts

App V3 `fetch_user_post_videos`：
- sort_type=0 latest
- sort_type=1 hottest
- cursor
- count <=20
- normal/lite channel fallback

检查：
- sec_user_id
- 作品 ID
- 指标
- 正序/最热实际一致性

### T08 Account Intelligence

仅用一个重点对标账号最小验证：
- account trends
- recent item analysis
- fan search terms
- fan topics
- fan portrait

确认：
- 普通账号是否都有数据
- 覆盖时间
- 成本
- 是否真的有研究价值

不进入 L0/L1。

### T09 Comments

- 默认 count
- 第一页
- cursor
- 热评排序实际行为
- comment id
- like count
- replies marker

不做全量翻页。

### T10 Comment Word Cloud

确认普通候选视频是否都能返回。
若覆盖不足，仅作为 Billboard 深研补充，不进入主链。

### T11 Cost / Usage

验证：
- get_endpoint_info
- calculate_price
- tiered discount
- daily usage
- 本地 external_api_call 对账

重点对比：
- batch video detail
- batch video statistics
- comments
- Creator related videos
- account intelligence

### T12 SDK vs REST Capability Diff

对 V1 endpoint catalog 做自动比对：

```text
online documented endpoint
vs
SDK generated method
```

输出：
- SDK_READY
- REST_FALLBACK
- DEPRECATED
- NOT_USED

### T13 SDK Retry / Failure

源码已知默认：
- timeout=30s
- max_retries=3
- retry 429 / 5xx / network
- Retry-After 优先

实测：
- invalid video id
- invalid account id
- HTTP 200 + TikHub body code != 200（如能构造）
- 429（若可安全触发）
- 5xx（若实际遇到）
- timeout

禁止为了测试 429 人为制造高请求量。

### T14 Normalization Replay

同一原始响应：
1. 保存 external_api_response
2. normalize 入业务表
3. 修改 normalizer 版本
4. 从 raw response 重跑 normalize

验收：
- 不重新调用 TikHub
- 核心实体不重复
- provider-independent canonical video/account ID 正确

## 输出

测试完成生成：
- `docs/tikhub/V0_ENDPOINT_MATRIX.md`
- `docs/tikhub/V0_COST_MATRIX.md`
- `docs/tikhub/SDK_REST_DIFF.md`
- `docs/tikhub/samples/*.json`（脱敏）
- Provider mapping
- price/call class
- recommended cache TTL
- recommended max pages
- production-ready endpoint allowlist
