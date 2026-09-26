# Windmill 手动评论采集 V1

更新：2026-09-26

## 结论

Flow 路径：

`f/content_research/flows/manual_comment_collection`

它只支持人工触发，不配置 schedule。默认 `execute=false`，此时只返回本次调用的技术上限和估算费用，不读取 TikHub Secret、不访问外部 API、不写采集结果。付费执行必须提供项目 ID，且视频已被项目接受、操作者拥有有效研究权限；采集运行及估算费用归到该项目。

## 正式执行的闸门

1. `execute=true`。
2. `confirmation` 必须精确等于 `COLLECT_COMMENTS_PAID`。
3. 必须提供有效项目 ID；待采视频必须已存在于 `source_video` 且已被该项目接受，当前登录者须是有效 Owner、Admin 或 Researcher。
4. 当天必须存在 `tikhub / windmill_manual_comments` 的计数行；`max_cost` 和 `max_requests` 可同时为空，表示不设置每日花费上限，但依然记录调用次数和估算费用。
5. PostgreSQL advisory lock 与 Windmill `concurrent_limit: 1` 同时限制并发。

任一条件不满足都会在读取 Secret 或发出外部请求前停止。TikHub 使用 REST 且客户端零重试。为避免一个作业无限挂起，单次运行技术上限为：

- `count <= 20`
- `max_pages <= 10`
- `max_items <= 200`
- 最多 10 个未缓存外部请求
- 按当前目录价估算，本次最多 0.010 USD

达到单次技术上限后可用返回的 `next_cursor` 继续下一段，没有项目累计采样量或费用上限。缓存命中不计外部调用；未缓存页在每次请求前由 PostgreSQL 行原子登记。`next_cursor` 只作分页令牌，不能证明样本代表性。

采集完成后从已存评论生成 L2 特征。如果特征生成失败，返回 `collected_feature_pending`，明确告知采集及费用已经落账，不能把它当成整次未执行而重新付费；以 `rebuild_features_only=true`、`confirmation=REBUILD_COMMENT_FEATURES` 仅从现有评论重建特征，不读取 TikHub Key、不发起外部请求。

## Secret 配置

GitHub Actions Secret 不会自动同步到 Windmill。由 Admin 在 Windmill 新建 Secret Variable：

`f/content_research/tikhub_api_key`

只填正式 Key 的值，不把值写进 Git、Flow 参数、运行说明、Issue、PR 或聊天。Flow 不把 Secret 作为输入参数；只有所有前置检查通过后，脚本才调用 `wmill.get_variable`。

Viewer/Operator 不应获得该 Variable 或数据库 Resource 的读取权限。由发布者身份执行 Backend runnable。

## 预算配置

若按当前业务试点“不设费用上限”，由 Admin 为当天写入仅记账、无封顶的行：

```sql
insert into daily_budget(
  budget_date, provider, budget_key, max_cost, max_requests
)
values (
  current_date, 'tikhub', 'windmill_manual_comments', null, null
)
on conflict(budget_date, provider, budget_key)
do update set
  max_cost=excluded.max_cost,
  max_requests=excluded.max_requests;
```

不要在采集 Flow 内自动创建计数行。若未来业务需要封顶，再给 `max_cost` 或 `max_requests` 赋值；本次试点不以低额上限替代业务验收。

## 返回与日志边界

Flow 只返回聚合字段：页数、缓存页数、外部页数、评论/观察计数、去重计数、特征状态、分页令牌和估算成本。项目账本按发现、评论、ASR、L3 分类展示，供应商日实扣与项目估算不能混称。

不返回：

- 视频、评论或用户 ID
- 昵称
- TikHub request ID
- raw response reference
- Key、余额或账户总额
- 内部 pipeline run ID

## 部署说明

Python runnable 使用固定 Git commit 安装项目包，必须更新到含项目运行归属及 L2 提取器的提交。Windmill 部署后需生成依赖 lock；升级项目包时必须显式修改 commit，并重新走测试与 PR。

先以 `execute=false` 检查预览。正式调用仍需人工填写确认字符串；本仓库不附带 schedule，也不从 GitHub Actions 自动触发该 Flow。
