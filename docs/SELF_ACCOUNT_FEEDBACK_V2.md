# V2 自有抖音账号效果回流

日期：2026-09-19

## 定位

V1 研究“外部世界什么在发生”。

V2 增加“我们自己发出去以后究竟发生了什么”。

两者必须区分。

## 数据源优先级

### 第一优先：抖音开放平台官方授权 API

官方当前已经提供授权账号的视频/账号数据能力，包括：

- 视频播放
- 点赞
- 评论
- 分享
- 平均播放时长
- 账号粉丝
- 主页访问
- 授权账号公开视频评论
- 经营授权下近 30 天视频数据等

优点：

- 官方 OAuth / business token
- 不需要把 Creator Platform Cookie 交给第三方
- 权限和账号关系清晰
- 更适合作为长期生产主链

限制：

- 需要申请相应能力
- 部分数据首次授权后次日才完整
- 某些接口只开放近 30 天
- 某些能力有开发者类型/应用类型准入要求
- 不能把授权能力用于外部平台级统计服务；我们的目标仅是内部分析自有账号

因此 V2 应先评估和申请官方能力。

### 第二优先：TikHub Creator V2（高阶补充）

只有官方 API 无法提供但研究价值明确的数据，才评估 TikHub Creator V2。

目前确认的额外能力包括：

- 逐秒/时间点留存曲线
- 点赞发生曲线
- 跳出曲线
- 流量来源
- 实际搜索关键词
- 更细观众画像
- 弹幕时间分布
- 投稿分析

但该接口要求把有效的 Douyin Creator Platform Cookie 发送给 TikHub。

这是高敏感凭据，不应该默认接入。

启用前必须完成：

1. 安全评估
2. TikHub 当前 Terms / Privacy 复核
3. Cookie 权限与失效测试
4. Secret 隔离
5. 日志脱敏
6. 最小调用范围
7. 决定是否接受第三方处理该凭据

如果风险不可接受，则放弃这些高阶字段，不影响 V2 基础闭环。

## 官方 OpenAPI 可形成的基础闭环

发布作品
→ 官方授权 OpenAPI
→ 播放 / 平均播放时长 / 赞评转 / 粉丝 / 主页访问
→ 与采用的 Pattern / Hook / 时长 / Story Card 关联
→ 效果比较
→ Pattern 验证 / 降级 / 淘汰

## TikHub Creator V2 可形成的增强闭环

在通过安全评估后：

发布作品
→ Creator V2
→ 逐秒留存 / 跳出 / 点赞点 / 流量源 / 搜索词 / 观众画像
→ 与 Hook / 反转 / 结构时间点对齐
→ 更精细地验证 Pattern

## 数据模型建议

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

其中逐秒/流量源/搜索词字段允许为空，以兼容“只接官方 OpenAPI”的模式。

## 安全原则

官方 OpenAPI：
- access token / business token 只放 Secret
- 使用官方 OAuth/授权流程
- 不写 Git
- 不暴露给 LLM

Creator V2 Cookie（若未来启用）：
- 视为高敏感凭据
- 单独 Secret
- 权限最小化
- 失效检测
- 不输出日志
- error payload 脱敏
- 不进入数据库 raw payload
- 不进入 prompt
- 不进入前端
- 必须能一键停用

## V2 不现在开发的原因

- V1 尚未进入自有账号发布闭环
- 官方能力申请和授权流程需要单独推进
- Cookie 模式存在额外安全风险
- V1 首要任务是把外部采集、筛选、历史库、Web、MCP 跑稳

但 V1 的 Pattern / Case / Story Card ID 设计从一开始保留未来关联能力。
