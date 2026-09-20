# 本机真实数据黄金链路（V1）

本入口将一页极小的公开抖音低粉榜数据送入既有 TikHub Provider、规范化、PostgreSQL、L0/L1 评分与 Windmill 研究台。它不新增绕过接口、爬虫或独立数据模型。

## 边界

- 默认 `dry-run`：不读 Secret、不连数据库、不触网。
- 实际运行只允许一页 `page=1`、最多 5 条视频、24 小时窗口。
- 默认最多 2 次未缓存外部调用：榜单一次，加最多 5 条视频的批量详情一次。
- SDK 和 REST transport 都设置零重试；异常即失败，不自动补发。
- Provider 在每个未缓存调用前向 `daily_budget(tikhub, golden-local)` 预留一次；缓存命中不消耗配额。L0/L1 Runner 明确不再重复预留。
- 视频唯一性使用既有 `(platform, platform_video_id)`；同一视频不会重复插入。每次运行仍保留独立 `pipeline_run` / `discovery_event` 审计记录。
- Provider 原始响应仅保存在本机 PostgreSQL 的 `external_api_response` 以支持重放，绝不写进 Git、命令标准输出或文档。

## 先看计划

```bash
uv run python scripts/tikhub/real_data_golden.py
```

它只输出无 Secret、无文本内容的调用上限与参数。

## 小额真实执行

仅在本机已有安全环境变量时执行；不要把它们写进 shell 历史、文件或 CI：

```bash
TIKHUB_GOLDEN_LIVE=YES \
TIKHUB_API_KEY='从本机 Secret 注入' \
DATABASE_URL='本机 PostgreSQL DSN' \
uv run python scripts/tikhub/real_data_golden.py --live
```

需要生成“本轮确有外呼”的可复核证据时，显式增加 `--force-refresh`。它只绕过发现页缓存，
仍受单次最多 2 次未缓存调用与零重试约束；按测试服当前授权不设固定金额上限，
但每次调用都会记录接口、缓存状态、成功/失败、已核验单价版本、币种和费用；聚合输出会分别记录本轮
`cached_call_count` 与 `uncached_call_count`，不输出正文、请求 ID 或 Secret。

命令仅输出 run ID、条数、评分数和上限。运行后可在 Windmill 研究台的视频库、运行/成本页查询该数据；页面通过既有 `source_video`、`metric_snapshot`、`pipeline_run` 和 `external_api_call` 查询，不需要改 TSX。

## 真实集成测试开关

普通 `pytest` 永不执行真实调用。需要一次受控端到端验证时：

```bash
RUN_REAL_TIKHUB_GOLDEN_INTEGRATION=YES \
TIKHUB_API_KEY='从本机 Secret 注入' \
DATABASE_URL='本机 PostgreSQL DSN' \
uv run pytest tests/test_real_data_golden_live.py -q
```

该测试仍采用相同的 5 条 / 2 调用 / 零重试预算，并核对 pipeline run、外部调用账本和当日预算。不要在共享 CI、测试服务器或没有确认成本的环境中设置该开关。

## 失败语义

- 没有 `TIKHUB_GOLDEN_LIVE=YES`：拒绝进入 live mode。
- 没有预算行、请求数超限、成本超限：在外部调用前失败关闭。
- 上游超时、429、余额不足、业务 envelope 非 200 或归一化失败：不重试，记录不含 Secret/原文的调用错误审计；run 标记失败。
- `page != 1`、超过 5 条、超过 2 调用：参数校验阶段拒绝。
