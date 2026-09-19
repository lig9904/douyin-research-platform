# V2 自有抖音账号效果回流

日期：2026-09-19

## 定位

V1 研究“外部世界什么在发生”。

V2 增加“我们自己发出去以后究竟发生了什么”。

这两者必须区分。

## 数据源

TikHub Douyin Creator V2。

前提：
- 使用我们自己的抖音创作者平台 Cookie
- Cookie 仅放 Windmill Secret
- 不写 GitHub
- 不暴露给前端
- 不暴露给 LLM
- 不使用任何第三方账号 Cookie

## 已确认可用的数据

### 作品观看趋势

可按时间点拿：
- 留存
- 点赞
- 跳出

官方说明播放量 > 200 后数据更准确。

价值：
- 直接验证 3 秒钩子是否留住人
- 找实际掉点
- 找实际点赞发生点
- 找反转前后留存变化

### 流量来源

可区分：
- 推荐页
- 朋友页
- 搜索
- 个人主页
- 消息
- 其他

价值：
- 判断内容是推荐流起量还是搜索/粉丝起量
- 避免把完全不同流量机制混在一起归因

### 搜索关键词

返回实际把用户带到作品的搜索词、次数、占比。

价值：
- 验证标题/话题/内容是否形成搜索资产
- 发现用户如何理解我们的角色和内容

### 观众画像

可获取：
- 活跃时段
- 性别
- 年龄
- 地域等

### 投稿表现

可按时间范围、体裁、垂类看作品表现。

### 投稿作品列表

每页最多 100 条；播放/赞评转藏等核心指标实时更新，其他指标约每小时更新（按当前官方文档）。

## V2 数据模型建议

新增：

- owned_account
- owned_post
- owned_post_metric_snapshot
- owned_post_retention_point
- owned_post_play_source
- owned_post_search_keyword
- owned_post_audience_snapshot
- owned_post_pattern_link
- owned_post_experiment

## 真正闭环

```text
外部 Case
  ↓
Pattern Candidate
  ↓
Story Card / Content Plan
  ↓
发布
  ↓
Creator V2 实际数据
  ↓
留存 / 跳出 / 点赞点 / 流量源 / 搜索词
  ↓
与采用的 Pattern / Hook / 时长 / 结构关联
  ↓
Pattern 验证、降级、淘汰或强化
```

## 为什么这个比公开数据“归因”强

公开数据只能看到：
- 播放/赞评转
- 评论
- 账号表现
- 外部趋势

自己的 Creator 数据可以看到：
- 观众具体在哪一秒离开
- 哪一秒点赞
- 流量到底从哪里来
- 用户搜什么词进来

所以未来真正验证 Pattern 的主要依据应是自有结果，而不是 LLM 的事后解释。

## 安全与运维

Creator Cookie 属于高敏感凭据。

必须：
- 单独 Secret
- 权限最小化
- 失效检测
- 定期轮换/重新登录
- 所有调用记录审计
- 不输出 Cookie 到日志
- error payload 脱敏

## V2 不现在开发的原因

- V1 尚未进入自有账号发布闭环
- Cookie 生命周期会增加运维
- V1 首要任务是把外部采集、筛选、历史库、Web、MCP 跑稳

但数据库与 Pattern/Case ID 设计从 V1 起要保留未来关联能力。
