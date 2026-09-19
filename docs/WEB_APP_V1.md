# Web Research App V1

日期：2026-09-19

## 技术形态

Windmill Full-code App + React。

路径建议：

```text
f/douyin_research/research_app.raw_app/
├── raw_app.yaml
├── package.json
├── index.tsx
├── App.tsx
├── pages/
├── components/
└── backend/
```

不建设独立 REST API Server。

## Backend Runnables

优先 PostgreSQL prepared statements：

- `get_home_summary.pg.sql`
- `search_all.pg.sql`
- `search_videos.pg.sql`
- `get_video_detail.pg.sql`
- `get_video_metric_history.pg.sql`
- `search_accounts.pg.sql`
- `get_account_detail.pg.sql`
- `get_account_history.pg.sql`
- `search_signals.pg.sql`
- `get_signal_detail.pg.sql`
- `get_cost_summary.pg.sql`

写操作使用 Python/TypeScript runnable，便于读取 `WM_END_USER_EMAIL`：

- `add_annotation.py`
- `add_to_collection.py`
- `remove_from_collection.py`
- `mark_priority.py`

## SQL 安全

PostgreSQL runnable 使用 prepared statement：

```sql
-- $1 query
-- $2 limit = 50
-- $3 offset = 0

select id, title, description, published_at
from source_video
where
  ($1 = '' or title ilike '%' || $1 || '%' or description ilike '%' || $1 || '%')
order by published_at desc nulls last
limit $2
offset $3;
```

禁止前端拼接原始 SQL。

表名/列名若必须动态选择，使用固定枚举，不接收任意字符串。

## 页面

### 1. 今日发现

显示：
- 今日新信号
- 新增视频
- 低粉爆款
- 高增长候选
- Creator 热点
- 今日重点 Case
- API/AI 当日成本

### 2. 视频库

筛选：
- 关键词
- 发布时间
- 首次发现时间
- 榜单来源
- L0/L1/L2/L3
- 分数区间
- 账号粉丝区间
- 收藏/专题
- 是否已深研

### 3. 热点/信号库

显示：
- 热点
- 话题
- 搜索词
- 同城热点
- Creator 热点
- Creator 话题/音乐

可直接进入相关视频。

### 4. 账号库

显示：
- 当前粉丝
- 历史粉丝快照
- 最近作品
- 最热作品
- 异常爆款数
- 被研究次数

### 5. Case 详情

一屏分层：

基础：
- 视频/账号/原链接
- 当前指标
- 指标历史

发现：
- 为什么被发现
- 来自哪个榜
- 当时排名/指标

研究：
- Transcript
- 评论
- 分析
- 人工备注

关联：
- 同热点相关视频
- 同账号作品
- 收藏/专题

### 6. 全局搜索

V1 搜：
- 视频标题/description
- 账号昵称
- 热点/话题
- 人工备注
- Transcript（已有时）

使用参数化 ILIKE/结构化筛选。

### 7. 收藏/专题

用户可建：
- 神话
- 剧情
- 反差
- 文旅
- 低粉黑马
- AI 原生
等专题。

### 8. 运行/成本

只给 Admin/Developer：
- TikHub 调用
- 成功/失败
- cache 命中
- 当日请求数
- 估算/实际成本
- LLM/ASR 成本
- 失败任务

## 权限

研究台默认 Members。

普通用户：
- Windmill Operator
- 仅 Research App read
- 不授予 TikHub/DB/LLM Secrets read

App backend 以 publisher 权限执行。

写人工标注时，actor 必须从：
`WM_END_USER_EMAIL`
获取真实用户身份。

## 前端原则

- 默认展示“人能读懂”的结论，不直接露原始 JSON
- 原始数据放折叠的“技术详情”
- 所有列表支持回到原始抖音链接
- 重要字段显示采集时间，避免把历史快照误当实时值
- 不展示伪精确“AI 98%匹配度”
