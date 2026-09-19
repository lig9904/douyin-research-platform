# TikHub 官方 Skills 可复用规则

日期：2026-09-19

来源：TikHub 官方 tikhub-plugin 中 trend-research、creator-analytics、competitor-analysis、comments-analysis、bulk-data-export、hashtag-research、douyin skills。

## 1. 直接借用的工程规则

### 成本先行

Bulk Data Export 官方 Skill 明确要求：
- 大批量拉取前先 estimate cost
- 每页都计费
- hard cap
- stable ID dedup
- 实际拉取数量必须回报

我们自动化后不需要每次等人工确认，但必须由 budget gate 代替人工确认。

### 分页上限

所有分页能力必须有：
- max_pages
- max_items
- max_cost

不得只写：
`while has_more`

### 外部 API 并发保守

官方 bulk skill 建议外部批量拉取时：
- concurrency cap <= 4
- 429/5xx backoff

V1 默认外部 TikHub 并发从 4 起步，真实限流测试后再调整。

本地 SQL/评分不受该限制。

### 稳定 ID 去重

边拉边按稳定平台 ID 去重：
- aweme_id
- sec_user_id
- comment id

不能用标题/昵称作为唯一键。

## 2. 对标账号分析必须公平

Competitor Analysis 官方 Skill 强调：
- 每个账号使用相同 post-count cap
- 尽可能使用相同时间窗
- 样本不足的账号必须标记
- 不要仅以 follower count 做结论

我们的代码层必须保存：
- sample_from
- sample_to
- sample_size
- requested_sample_size
- completeness

账号比较默认使用：
- median views
- median interactions
- posts/week
- normalized engagement
- hit ratio
- growth signals

不要让 LLM自行挑不同数量的作品比较。

## 3. 单账号基线

Creator Analytics 官方 Skill建议：
- recent 30–100 posts，页数有 cap
- engagement rate
- posting cadence
- top posts
- median vs top
- trend over time

可借鉴，但我们的黑马系统不能只靠平均值。

推荐保存：
- median
- p75/p90
- MAD / robust deviation
- recent N
- age-adjusted metrics

官方 Skill 的：
`avg(likes + comments + shares) / followers`
只能作为一个可解释指标，不作为唯一“优劣”标准。

## 4. 评论分析的抽样纪律

官方 Comments Analysis 强调：
- 每个 comment/reply page 都计费
- report sample size
- 50条评论不能冒充50k评论总体
- reply pagination 不能无限

我们的 Case 必须显示：
- total_comment_count（若可得）
- sampled_comment_count
- sampling_strategy
- pages_fetched
- sample_ratio（可算时）

评论 LLM 结论必须明确基于样本，而不是“整个评论区”。

## 5. Hashtag / Keyword

官方 Hashtag Research 规定：
- top posts 必须真正与关键词/标签匹配
- related tags 来自真实共现，不应让模型猜
- API没有 volume 就明确 unavailable，不能发明数字

因此相关词/标签：
1. Index relation-word / API
2. top-post真实共现统计
3. embedding cluster
4. 最后才由LLM解释

## 6. Douyin Search

TikHub 官方 Douyin Skill 明确：
- 专用 Search series 优先于 App V3 search
- Search V2 是 POST + JSON body
- user endpoints需要 sec_user_id
- page/cursor 每页收费，必须 cap

Provider adapter 应隐藏这些接口细节，业务层只看到统一 search_videos/search_accounts。

## 7. Trend Research 借鉴点

官方 Trend Research 的核心不是“让AI总结”，而是：
- 明确 region
- 明确 period
- 拉排行榜
- normalize
- 不混用不同地域/时间窗而不标注

我们 Structured Intelligence 必须把：
- region
- window/period
- captured_at
放进数据对象。

## 8. 不直接照搬的部分

官方 Skills 是交互式 Agent 工作流，适合临时任务；我们的平台是长期自动系统。

所以不直接照搬：
- 每次任务人工确认 cost
- Agent自由选择endpoint
- Agent自由继续翻页
- Agent直接做最终“排名/机会”判断
- 临时输出后不留历史

我们替换为：
- budget policy
- read-only allowlist
- fixed provider methods
- persistent snapshots
- deterministic scoring
- repeatable analysis versions

## 9. 需要进入测试的规则

V0/V1测试必须覆盖：
- external API concurrency=4 时是否稳定
- same-sample account comparison
- comment sample metadata
- duplicate ID removal
- bounded pagination early stop
- cost cap stops pagination
- region/window不会在聚合时被丢掉
