# V0 TikHub API 实测矩阵

日期：2026-09-19

> 目标：拿到 API Key 后按本表逐项验证。未通过实测的接口不进入生产代码。

状态值：
- PENDING：未实测
- PASS：通过
- PARTIAL：可用但有限制
- FAIL：不采用

## A. 免费预验证

| 能力 | Endpoint / SDK | 认证 | 重点验证 | 状态 |
|---|---|---|---|---|
| 固定 Douyin APP 视频 | Demo APP fixed video | 无 | 网络、response envelope、raw保存、normalizer | PASS |
| 固定 Douyin Web 视频 | Demo Web fixed video | 无 | Web/App结构差异 | PASS |
| 固定综合搜索 | Demo general search | 无 | search envelope、cursor/has_more、稳定 aweme_id | PASS |
| Demo cache status | Demo cache status | 无 | cache字段含义 | PASS |

注意：SDK 构造器强制 API Key，因此无 Key Demo 用 httpx/curl。

2026-09-19 免费实测摘要：

- 四个 Demo 均返回 HTTP 200、TikHub `code=200`、`request_id` 与对应 `router`。
- App/Web 固定视频均有 `aweme_id`、`author.sec_uid`、`duration`，且都存在 `statistics.play_count`。
- 综合搜索返回 19 个顶层卡片，带 `cursor=20`、`has_more=1`、`backtrace`，卡片内可提取稳定 `aweme_id`。
- cache status 返回 `total_cached_items` 与逐项 `expires_in_seconds` / `is_expired`。
- 上述只证明免费固定样本与响应结构可用；普通账号覆盖率、字段稳定性、准确性和计费仍保持 PENDING，必须用正式 Key 实测。

## B. Billboard / 发现层

| 能力 | 预期用途 | L层 | 实测重点 | 生产频率候选 | 状态 |
|---|---|---:|---|---|---|
| 视频总榜 | 广泛发现 | L0 | ID、指标、分页/数量、刷新 | 30–60m | PENDING |
| 低粉爆款 | 黑马主信号 | L0 | 粉丝/播放/互动字段、tag/date_window | 15–30m | PENDING |
| 高完播 | 结构候选 | L0 | 完播指标定义、覆盖率 | 30–60m | PENDING |
| 高涨粉 | 账号/内容起量 | L0 | 涨粉指标定义 | 30–60m | PENDING |
| 高点赞 | 强互动候选 | L0 | 点赞指标/排序 | 30–60m | PENDING |
| 上升热点 | 实时热点 | L0 | 热度、排名、时间字段 | 15–30m | PENDING |
| 同城热点 | 秦皇岛本地 | L0 | city_code、覆盖率、刷新 | 30–60m | PENDING |
| 上升搜索 | 搜索趋势 | L0 | query、热度、变化 | 30–60m | PENDING |
| 上升话题 | 话题趋势 | L0 | topic ID/标题/热度 | 30–60m | PENDING |
| 热门账号 | 新对标候选 | L0 | sec_uid、粉丝、分类 | 1–6h | PENDING |
| 评论词云 | 评论粗筛 | L2 | 普通视频是否可用、词权重 | 按需 | PENDING |
| 作品数据趋势 | 重点Case补充 | L2 | 指标、历史跨度、粒度 | 按需 | PENDING |
| 点赞观众画像 | 热榜样本画像 | L2 | 是否严格限热榜、字段 | 按需 | PENDING |
| 账号粉丝趋势 | 对标账号背景 | L2 | 任意sec_uid可用性 | 按需 | PENDING |
| 上周作品分析 | 对标账号背景 | L2 | 统计粒度 | 按需 | PENDING |
| 粉丝画像/兴趣 | 用户结构 | L2 | 覆盖率、空数据条件 | 按需 | PENDING |

## C. Search / Index

| 能力 | 用途 | L层 | 实测重点 | 状态 |
|---|---|---:|---|---|
| 普通视频搜索 | 关键词研究 | L0/L1 | search_id/cursor、分页、排序 | PENDING |
| 普通账号搜索 | 低粉账号/对标 | L0/L1 | 粉丝档筛选、分页、ID | PENDING |
| Index 视频查询 | 结构化样本池 | L0/L1 | low-fan/high-completion等筛选 | PENDING |
| Index filter options | 动态枚举 | L0 | tags/categories是否稳定 | PENDING |
| 多关键词热度趋势 | 趋势事实层 | Intelligence | 日期、地域、指标 | PENDING |
| 多关键词指数解读 | 指数事实层 | Intelligence | composite/search/content含义 | PENDING |
| 关联词 | 词网络 | Intelligence | 搜索词/内容词结构 | PENDING |
| 关键词人群画像 | 用户画像 | Intelligence | 性别/年龄/地域/兴趣 | PENDING |
| 垂类热门话题 | 赛道趋势 | Intelligence | period、排序、category | PENDING |
| 垂类热门关键词 | 赛道趋势 | Intelligence | period、字段 | PENDING |
| 发布趋势 | 创作供给 | Intelligence | 时间粒度 | PENDING |
| 创作时长分布 | 时长事实 | Intelligence | buckets、period | PENDING |
| 创作者画像 | 供给画像 | Intelligence | category覆盖 | PENDING |
| 消费者画像 | 受众画像 | Intelligence | week/month/date约束 | PENDING |
| 互动趋势 | 互动事实 | Intelligence | 指标定义 | PENDING |
| 消费趋势 | 播放/时长/UV | Intelligence | total plays/watch/UV | PENDING |
| 相似达人 | 对标拓展 | L2 | 输入ID、覆盖率 | PENDING |
| 达人趋势/核心指标 | 账号比较 | L2 | 指标与日期 | PENDING |
| 视频指数趋势 | Case事实 | L2 | 指数与公开metrics关系 | PENDING |
| 视频对比/观众分析 | Case比较 | L2 | 覆盖条件 | PENDING |

## D. App V3 / 详情层

| 能力 | 用途 | L层 | 实测重点 | 状态 |
|---|---|---:|---|---|
| 批量视频详情 V2 | metric snapshot主链 | L1/L2 | 50条上限、字段完整性、删除/私密状态 | PENDING |
| 单视频详情 | fallback | L2 | 与batch字段差异 | PENDING |
| 用户详情 | 重点账号补充 | L2 | follower等指标 | PENDING |
| 用户作品 normal | 账号监控 | L1 | 最新作品、cursor | PENDING |
| 用户作品 lite | normal fallback | L1 | 与normal差异 | PENDING |
| 视频评论 | 重点Case | L2/L3 | default count、cursor、排序 | PENDING |
| 评论回复 | 深研 | L3 | pagination/成本 | PENDING |
| 分享链接解析 | 人工提交URL | L1 | URL类型兼容 | PENDING |

## E. Creator Center / 额外发现

| 能力 | 用途 | L层 | 实测重点 | 状态 |
|---|---|---:|---|---|
| 热门视频榜 | 旅行/剧情/二次元等 | L0 | category、24h/7d/30d、ID衔接 | PENDING |
| 热门话题榜 | 话题发现 | L0 | query/topic ID | PENDING |
| 创作热点榜 | 热点发现 | L0 | 热度/分类 | PENDING |
| 同城/热点相关视频 | 本地信号 | L0 | city/地域 | PENDING |
| 热门音乐/挑战/道具 | 后置可选 | L0 | 实际研究价值 | PENDING |

## F. 计费/账户

| 能力 | 重点验证 | 状态 |
|---|---|---|
| calculate_price | 每个生产endpoint的真实阶梯报价 | PENDING |
| get_user_daily_usage | 与本地 external_api_call 对账 | PENDING |
| 429 | Retry-After / SDK异常类型 / 是否计费 | PENDING |
| 402/余额不足 | SDK异常类型、是否重试 | PENDING |
| 5xx | SDK重试次数、实际退避 | PENDING |

## G. 每个接口必须保存的实测结果

每一项 PASS/PARTIAL 必须记录：

- SDK method
- REST endpoint
- auth type
- request params
- raw response fixture（脱敏）
- stable video/account/topic/query IDs
- metrics available
- missing fields
- null/zero语义
- pagination
- batch/page limit
- freshness/cache
- coverage restrictions
- rate-limit behavior
- retry behavior
- price / discount tier
- expected use layer
- recommended polling interval
- fallback
- provider field -> internal field mapping

## H. 冻结条件

只有以下全部完成，才冻结 Provider / Schema mapping：

1. Billboard 主发现源 PASS
2. 至少一个 Search + 一个 Index 路径 PASS
3. App V3 batch detail PASS
4. normal/lite账号作品策略 PASS/PARTIAL且有fallback
5. 评论第一页 PASS
6. calculate_price + usage PASS
7. 429/5xx至少做一次受控故障测试
8. ID在 Billboard/Index/Search/App V3 之间可稳定关联
9. null/缺失/删除/私密状态有统一映射
