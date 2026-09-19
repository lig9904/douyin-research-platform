
# V1 成本模型与轮询策略

日期：2026-09-19

## 1. TikHub 计价基线

TikHub 官方当前说明：大多数基础接口基础价为 0.001 USD/request；每日请求量按阶梯折扣。系统可调用 calculate_price(endpoint, request_per_day) 获取特定 endpoint 的报价，并用 get_user_daily_usage 获取实际日用量。

官方阶梯示例：
- 0–999/day：0%
- 1000–4999/day：10%
- 5000–9999/day：20%
- 10000–19999/day：30%
- 20000–29999/day：40%
- 30000+/day：50%

以下预算只是按 0.001 USD 基础接口做容量估算；V0 必须对实际 endpoint 再校正。

## 2. 成本优化优先级

1. 禁止无限分页
2. 详情批量化
3. 去重后才补详情
4. 只有新增/变化对象进入后续 Flow
5. 评论只取样本
6. ASR 按需
7. LLM 只用于 L3
8. 媒体下载默认关闭

## 3. 建议轮询频率

热点发现：
- 核心 Billboard：15–30 分钟
- 非核心榜单：30–60 分钟
- 夜间可降频至 60 分钟
- Index / 关键词研究：2–6 小时，或人工触发

视频快照采用生命周期自适应：
- 0–6h：1h
- 6–24h：2–3h
- 1–3d：6–12h
- 3–7d：24h
- >7d：默认停止；重点 Case 例外

对标账号：
- A 级重点：1–2h
- B 级：6h
- C 级：24h
默认只拉作品第一页；发现新作品才继续补详情。

评论：
- L0/L1：不拉
- L2：优先词云或热门评论第一页
- L3：根据研究需求继续分页
所有分页必须有 max_pages、max_items、max_cost_usd。

## 4. 粗略成本场景

起步场景：
- 发现/榜单约 256 calls/day
- Search/Index 约 40/day
- 500 个活跃候选每天 4 次快照；50 条/批次约 40/day
- 100 个对标账号每日 1 页约 100/day
- 深研评论约 20/day
- usage/price 等约 4/day

合计约 460 基础请求/day：
- 约 0.46 USD/day
- 约 13.8 USD/30 days

正常运营场景：
- 约 900–1200 requests/day
- 粗估约 0.9–1.2 USD/day
- 约 27–36 USD/month

较重研究场景：
- 约 2500 基础 requests/day
- 按官方阶梯示例约 2.35 USD/day
- 约 70.5 USD/30 days

上述均不包含特殊高价 endpoint、ASR、LLM、媒体下载。

## 5. 为什么快照可以做得比较密

App V3 batch video detail 一次最多 50 个视频，固定 0.001 USD/request。

1000 个候选视频：
- 20 个 batch = 0.02 USD/一轮
- 每天 6 轮 = 0.12 USD/day

因此增长速度应优先用真实 metric_snapshot 计算，而不是用 LLM 猜趋势。

## 6. 账号 Profile 优化

Douyin Web 当前有 batch user profile V2，一次最多 50 个 sec_user_id。

V0 需要实测其稳定性和字段完整性。如果稳定，可低成本刷新账号粉丝量等基础信息；如果 batch video detail 已带足够 author 统计，则进一步减少 profile 请求。

## 7. 预算闸门

每个 provider / endpoint 设置：
- hard_limit_requests
- hard_limit_cost
- soft_limit_pct
- priority
- enabled

调用流程：
cache -> budget check -> rate bucket -> TikHub SDK -> external_api_call -> business DB/cache

建议：
- 80%：警告
- 95%：只允许高优先级
- 100%：拒绝非 emergency 调用

任务优先级：
- P0 realtime：新热点/黑马
- P1 high：活跃候选快照
- P2 normal：对标账号/补评论
- P3 backfill：历史补数

预算紧张时按 P3、P2、P1 顺序降级，尽量保留 P0。

## 8. 成本对账

每天：
1. 汇总 external_api_call
2. 调 TikHub get_user_daily_usage
3. 比较本地统计与供应商统计
4. 保存差异
5. 超阈值报警

研究台展示：
- 今日请求
- 今日预估成本
- TikHub 实际 usage
- 本月累计
- 本月预测
- endpoint 成本占比

## 9. 成本重点

按当前能力，TikHub 数据接口不是最可能的大头。长期更应重点监控：
- ASR
- LLM
- 未来视觉分析
- 媒体下载/存储

所以成本面板必须分项，而不是只显示 TikHub。
