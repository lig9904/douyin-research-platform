# Windmill 手动评论采集 V1

日期：2026-09-19

## 结论

Flow 路径：

`f/content_research/flows/manual_comment_collection`

它只支持人工触发，不配置 schedule。默认 `execute=false`，此时只返回调用上限和成本上限，不读取 TikHub Secret、不访问外部 API、不写采集结果。

## 正式执行的五重闸门

1. `execute=true`。
2. `confirmation` 必须精确等于 `COLLECT_COMMENTS_PAID`。
3. 待采视频必须已存在于 `source_video`。
4. 当天必须预先配置 `tikhub / windmill_manual_comments` 的 `daily_budget`。
5. PostgreSQL advisory lock 与 Windmill `concurrent_limit: 1` 同时限制并发。

任一条件不满足都会在读取 Secret 或发出外部请求前停止。TikHub SDK `max_retries=0`，单次运行硬限制为：

- `count <= 20`
- `max_pages <= 2`
- `max_items <= 40`
- 最多 2 个未缓存外部请求
- 按当前目录价估算，最多 0.002 USD

缓存命中不占用外部调用预算；未缓存页在每次请求前由 PostgreSQL 预算行原子预留。

## Secret 配置

GitHub Actions Secret 不会自动同步到 Windmill。由 Admin 在 Windmill 新建 Secret Variable：

`f/content_research/tikhub_api_key`

只填正式 Key 的值，不把值写进 Git、Flow 参数、运行说明、Issue、PR 或聊天。Flow 不把 Secret 作为输入参数；只有所有前置检查通过后，脚本才调用 `wmill.get_variable`。

Viewer/Operator 不应获得该 Variable 或数据库 Resource 的读取权限。由发布者身份执行 Backend runnable。

## 预算配置

正式运行前，由 Admin 为当天写入预算。建议首轮保持最小值：

```sql
insert into daily_budget(
  budget_date, provider, budget_key, max_cost, max_requests
)
values (
  current_date, 'tikhub', 'windmill_manual_comments', 0.002, 2
)
on conflict(budget_date, provider, budget_key)
do update set
  max_cost=excluded.max_cost,
  max_requests=excluded.max_requests;
```

不要在采集 Flow 内自动创建或扩容预算。预算耗尽时应停止，由 Admin 独立判断是否调整。

## 返回与日志边界

Flow 只返回聚合字段：页数、缓存页数、外部页数、评论/观察计数、去重计数和估算成本。

不返回：

- 视频、评论或用户 ID
- 昵称
- TikHub request ID
- cursor
- raw response reference
- Key、余额或账户总额
- 内部 pipeline run ID

## 部署说明

Python runnable 使用固定 Git commit 安装项目包，确保 Provider、缓存、预算和证据入库逻辑与 PR #20 的已验证版本一致。Windmill 部署后会生成依赖 lock；升级项目包时必须显式修改 commit，并重新走测试与 PR。

先以 `execute=false` 检查预览。正式调用仍需人工填写确认字符串；本仓库不附带 schedule，也不从 GitHub Actions 自动触发该 Flow。
