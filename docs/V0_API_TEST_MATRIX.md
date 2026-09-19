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

## A2. 正式 Key 首轮受控验证

2026-09-20 本机最小复验：

- 仅调用一次低粉爆款榜首屏，参数固定为 `page=1`、`page_size=5`、`date_window=24`、空 tags。
- TikHub SDK 2.1.1，`max_retries=0`；返回 `code=200`、request ID 和正确 router，首个列表包含 4 项。
- Secret 从已登录账号的现有密钥临时注入进程环境，未创建新 key，未显示、未写入 Git 或测试输出。
- 原始响应只保存在 gitignored 的本机 `tmp/tikhub-v0/`，提交与 CI 不包含该响应。

这次复验只证明当前本机认证和该单个榜单路径可用，不扩大原有 PARTIAL 结论。

2026-09-19 GitHub Actions [paid smoke #2](https://github.com/lig9904/douyin-research-platform/actions/runs/35436170460)：

- TikHub SDK 版本：2.1.1。
- 安全测试：10 项通过后才注入正式 Key。
- 最多四次平台调用，无分页；四项均返回 TikHub `code=200` 和 request ID。
- 低粉爆款榜首屏返回 4 项。
- 创作者中心旅行素材榜（24h）返回 20 项。
- “神话”视频搜索 V2 首屏返回 5 项。
- App V3 批量视频详情用 1 个公开作品 ID 返回 1 项。
- 日志仅保留响应形状；未输出 API Key、完整 request ID、账号 ID 或作品 ID。
- Runner 中的原始响应已清理且未上传。

结论边界：本轮证明认证、SDK 调用路径和四个端点当前可用，因此对应矩阵标记为 PARTIAL；尚未证明字段完整性、普通小号/中腰部/星图覆盖率、分页稳定性、删除/私密映射、准确性、计费和长期稳定性，不能标记 PASS，也不满足冻结条件。

## A3. 正式 Key 第二批：账号到评论链路

2026-09-19 第二批累计严格限制为 10 次请求：

- [首次运行](https://github.com/lig9904/douyin-research-platform/actions/runs/35436732311)完成 3 次：daily usage、评论端点 calculate_price、账号搜索 V2 均返回 `code=200`；因 V2 响应未被原解析器识别出稳定 `sec_uid`，程序立即停止，后续 7 次未执行。
- PR #15 增加字符串化嵌套 JSON 解析、账号搜索 V1 恢复路径和独立 `BATCH2_RESUME7` 七调用硬闸门。
- [恢复运行](https://github.com/lig9904/douyin-research-platform/actions/runs/35436920483)完成 7 次；两次合计正好 10 次，SDK 重试为 0。
- 账号搜索 V1 返回 10 个稳定候选，自动选择到 1万–10万粉丝档的公开普通账号。
- 用户详情返回粉丝数、关注数、总获赞字段。
- 用户作品第一页返回 11 个唯一视频，且 11/11 均存在播放、点赞、评论、分享、收藏指标；同时返回 cursor 和 `has_more=1`。
- App V3 批量详情对其中 1 个视频返回 1 项，五项核心指标均存在。
- 评论第一页返回 20 条唯一评论，同时返回 cursor 和 `has_more=1`。
- daily usage 前后共有 6 个数值字段发生变化，包含请求量与余额用量相关字段；日志不公开账户总量或余额值。
- 所有日志仅保留字段存在性、数量、粉丝档位和分页信号；未输出 API Key、完整 request ID、账号/作品/评论 ID 或昵称。原始响应未上传并已清理。

结论边界：账号搜索、账号详情、账号作品、普通作品播放量、批量详情、评论第一页、calculate_price 和 daily usage 已获得真实证据，但样本量仍小；第二页、评论回复、删除/私密作品、星图账号、更多粉丝档、准确性对照及数据库账单对账尚未完成，因此均保持 PARTIAL。

## A4. 正式 Key 第三批：翻页、去重与评论回复

2026-09-19 GitHub Actions [第三批运行](https://github.com/lig9904/douyin-research-platform/actions/runs/35437303600)：

- PR #17 的 provider-tests 先通过；正式运行前安全测试 20 项通过。
- 本批恰好完成 10 次平台请求，SDK `max_retries=0`，未发生隐式重试。
- 账号搜索返回 10 个稳定候选，并自动选择到 1万–10万粉丝档公开普通账号。
- 用户作品第一页 11 条、第二页 10 条；两页合计 21 条唯一作品，跨页重复 0，第二页 cursor 发生推进，第二页仍有更多数据。
- 两页作品的播放、点赞、评论、分享、收藏字段覆盖均为 100%。
- 评论第一页 20 条、第二页 19 条；两页合计仅 29 条唯一评论，跨页重复 10 条。结论是评论 cursor 可推进，但不能假设页面互斥，生产采集必须按稳定评论 ID 去重。
- 自动优先选择了服务端报告存在回复的评论；评论回复第一页返回 15 条唯一回复，`has_more=0`。
- `calculate_price` 对评论端点返回公开基础单价 0.001；按 100 次/日计算总价为 0.1，并返回 0%–50% 的阶梯折扣信息。该结果只证明当前报价响应，不代替账单对账。
- daily usage 前后检测到 10 个数值字段变化，覆盖本批调用的端点用量；日志未公开余额或账户总量。
- 日志只保留数量、覆盖率、重复数、布尔值、粉丝档位与公开报价；未输出 API Key、完整 request ID、账号/作品/评论 ID、cursor 或昵称。原始响应未上传且已清理。

结论边界：作品翻页、评论翻页、跨页去重要求和评论回复已获得真实证据，评论回复由 PENDING 提升为 PARTIAL。由于仍是单账号/单作品小样本，且尚未进行删除/私密映射、更多账号分层、Ground Truth 准确性、429/5xx 和本地账单对账，相关能力均不标记 PASS，Issue #1 继续保持开放。

## B. Billboard / 发现层

| 能力 | 预期用途 | L层 | 实测重点 | 生产频率候选 | 状态 |
|---|---|---:|---|---|---|
| 视频总榜 | 广泛发现 | L0 | ID、指标、分页/数量、刷新 | 30–60m | PENDING |
| 低粉爆款 | 黑马主信号 | L0 | 粉丝/播放/互动字段、tag/date_window | 15–30m | PARTIAL |
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
| 普通视频搜索 | 关键词研究 | L0/L1 | search_id/cursor、分页、排序 | PARTIAL |
| 普通账号搜索 | 低粉账号/对标 | L0/L1 | 粉丝档筛选、分页、ID | PARTIAL |
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
| 批量视频详情 V2 | metric snapshot主链 | L1/L2 | 50条上限、字段完整性、删除/私密状态 | PARTIAL |
| 单视频详情 | fallback | L2 | 与batch字段差异 | PENDING |
| 用户详情 | 重点账号补充 | L2 | follower等指标 | PARTIAL |
| 用户作品 normal | 账号监控 | L1 | 最新作品、cursor | PARTIAL |
| 用户作品 lite | normal fallback | L1 | 与normal差异 | PENDING |
| 视频评论 | 重点Case | L2/L3 | default count、cursor、排序 | PARTIAL |
| 评论回复 | 深研 | L3 | pagination/成本 | PARTIAL |
| 分享链接解析 | 人工提交URL | L1 | URL类型兼容 | PENDING |

## E. Creator Center / 额外发现

| 能力 | 用途 | L层 | 实测重点 | 状态 |
|---|---|---:|---|---|
| 热门视频榜 | 旅行/剧情/二次元等 | L0 | category、24h/7d/30d、ID衔接 | PARTIAL |
| 热门话题榜 | 话题发现 | L0 | query/topic ID | PENDING |
| 创作热点榜 | 热点发现 | L0 | 热度/分类 | PENDING |
| 同城/热点相关视频 | 本地信号 | L0 | city/地域 | PENDING |
| 热门音乐/挑战/道具 | 后置可选 | L0 | 实际研究价值 | PENDING |

## F. 计费/账户

| 能力 | 重点验证 | 状态 |
|---|---|---|
| calculate_price | 每个生产endpoint的真实阶梯报价 | PARTIAL |
| get_user_daily_usage | 与本地 external_api_call 对账 | PARTIAL |
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
