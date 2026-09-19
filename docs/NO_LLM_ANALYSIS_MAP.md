# 代码/结构化数据优先分析映射

日期：2026-09-19

## 目标

把常见研究问题映射到“现成结构化接口 + SQL/代码”，防止后续 Agent 图省事到处调用 LLM。

规则：

1. 平台已有结构化结果 -> 直接用
2. 能由 SQL/统计计算 -> 自己算
3. 能由 embedding/聚类解决 -> 不用生成模型
4. 只有语义、叙事、复杂机制解释才进入 LLM

## 1. 热点与趋势

### 问：现在什么热点在上升？

不用 LLM。

优先：
- Billboard rising hot list
- Billboard rising topic list
- Billboard rising search list
- Creator hot spot billboard

代码：
- 排名变化
- 首次出现时间
- 连续在榜时长
- category/city 过滤

### 问：某几个关键词谁更热、涨得更快？

不用 LLM。

优先：
- Index fetch_multi_keyword_hot_trend
- Index fetch_multi_keyword_interpretation

代码：
- slope
- WoW/DoD
- 峰值
- 波动率
- 地域差

### 问：一个关键词周围关联什么内容？

不用 LLM 起步。

优先：
- Index fetch_relation_word

得到：
- 搜索关联词
- 内容关联词
- 关联图

LLM 只有在需要解释“这些词为什么形成某种文化/叙事聚类”时才用。

## 2. 垂类趋势

### 问：剧情/旅行/二次元最近更偏什么时长？

不用 LLM。

- Index fetch_content_creative_duration

### 问：垂类消费是否变热？

不用 LLM。

- Index fetch_content_consume_trend

可直接计算：
- 播放增长
- 观看时长增长
- UV增长

### 问：垂类互动是否变强？

不用 LLM。

- Index content interact trend

### 问：垂类最近创作者在做什么？

先不用 LLM。

- content creative keywords
- content creative topic
- content publish trend
- Creator material center billboard

只有进入“这些趋势背后的叙事/文化机制”才用 LLM。

## 3. 用户画像

### 问：某关键词的人群是谁？

不用 LLM。

- Index fetch_portrait

### 问：某垂类观众是谁？

不用 LLM。

- Index fetch_content_consumer_portrait

### 问：某热门视频的点赞观众是谁？

先尝试：
- Billboard work-like audience portrait（仅热门榜）

不要让 LLM 根据评论昵称猜年龄/性别/地域。

## 4. 对标账号

### 问：哪个账号是黑马？

不用 LLM。

数据：
- 粉丝
- 视频播放/赞评转
- 账号历史中位数
- 发布后增长
- Billboard/Index标签

代码：
- robust z-score / median ratio
- follower-normalized performance
- growth velocity
- repeat hit rate

### 问：账号最近整体表现如何？

先结构化：
- Billboard account work analysis
- Billboard account trend
- Index daren core metrics / compare users（V0验证）
- 自己保存的 account baseline

### 问：哪些账号相似？

优先：
- Index similar daren（V0验证）
- 我们自己的 embedding / profile features

不用生成模型逐账号写描述再比较。

### 问：账号粉丝偏好什么？

优先尝试：
- fan portrait
- fan interest authors
- fan interest topics
- fan interest search terms

只有结构化数据无法解释时才用评论/内容语义。

## 5. 视频表现

### 问：视频是不是异常爆发？

不用 LLM。

代码：
- current / account median
- velocity
- engagement
- rank cross-hit
- age-adjusted score

### 问：两条视频谁表现更强？

不用 LLM做数字判断。

优先：
- normalized metrics
- same-age snapshot
- Index video comparison（V0验证）

LLM只能解释内容差异，不能负责算指标。

### 问：视频的趋势是什么？

优先：
- 自己的 metric_snapshot
- Index video index trend（V0验证）
- Billboard post trend（重点Case）

不用LLM读几个快照后“判断涨势”。

## 6. 评论

### 问：评论区高频在说什么？

先：
- Billboard comment wordcloud
- token frequency
- n-gram
- emoji stats
- high-like comment sampling

### 问：评论分成几类？

先：
- embedding
- clustering
- representative comments

### 问：为什么某类评论产生情绪共鸣？

这才进入 LLM。

## 7. 内容时长/节奏

### 问：视频多长、多少句、说话速度？

不用 LLM。

- ffprobe
- ASR timestamps
- Python

计算：
- duration
- words/sec
- sentence duration
- silence ratio（后续）
- hook window word count

### 问：3秒内是否发生语言钩子？

第一层代码：
- 前3秒 transcript
- 问号/异常词/数字/否定/结果词规则

真正判断“这个钩子的语义功能”才用 LLM。

## 8. 画面信息

### 问：分辨率、镜头切换、字幕有多少？

不用 LLM。

- ffprobe
- FFmpeg scene detection
- PaddleOCR（后续）

### 问：画面中的动作为什么构成反转？

才用多模态 LLM。

## 9. 周报

事实层完全代码生成：

- 本周新增热点
- 黑马数
- 各Profile数量
- 上升关键词
- 时长分布
- 消费趋势
- 热门话题
- Top账号
- Top Case
- API/ASR/LLM成本

LLM只负责：
- 把事实层压缩成自然语言
- 提炼跨Case机制假设
- 指出需要进一步人工研究的问题

## 10. LLM 允许处理的问题

允许：

- Hook语义功能
- 叙事结构
- 角色关系
- 反转机制
- 情绪机制
- 评论深层语义
- 多案例共同机制
- 反例比较
- IP适配
- Pattern candidate
- 新选题/梗概

不允许：

- 加减乘除
- 排序
- 比率
- 增速
- 去重
- 日期
- 分页
- 关键词精确匹配
- 画像数字统计
- 趋势曲线计算
- 成本计算

## 11. 工程约束

每个 analysis task 增加：

```text
resolution_method:
  structured_api | sql | code | embedding | llm
```

若 resolution_method=llm，必须同时记录：

```text
llm_reason
```

用于成本审计。

这样以后可以统计：
- 本月多少分析本可不用 LLM
- 哪个 Flow 最浪费 token
- 哪些 LLM 任务可以降级为代码
