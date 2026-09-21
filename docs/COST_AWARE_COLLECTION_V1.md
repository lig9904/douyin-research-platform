# 费用事实与省费采集实现

日期：2026-09-20。实现范围：Provider、报价规划、调用日志、只读费用页与隔离测试。
**尚未部署本轮改动；新路由没有完成真实字段覆盖／扣费验收，默认仍为既有 batch50。**

## 2026-09-21 单条详情实测与边界修正

测试服务器经 JumpServer 执行一次 `douyin.app.one_video` 请求，零重试、未命中缓存；
既有响应存档 `external_api_response.id=19`，服务器日志
`/srv/douyin-research-test/evidence/single-detail-live-20260921.log`。
已观察 HTTP 尝试 1 次，报价估算 USD 0.001，不是供应商确认扣款。
没有调用 ASR/LLM，没有将此次详情写入业务视频表。

响应顶层 `code=200`、数据层 `status_code=0`，但 `aweme_details=null`，
`filter_list` 只有目标 ID 与 `reason=7`。这证明本次没有可用详情，不能推断视频已删除，
也不能将 HTTP 成功或探针退出码 0 当作详情验收通过。旧通用解析器误把过滤记录识别为视频，
本次修正为在视频解析时递归排除 `filter_list` 与 `verification_filter_list`，包括 JSON 字符串嵌套。
有效详情及真实零指标仍保留；评论与分页遍历不改变。

同时修正显式 `cost_aware` 计划的运行上限：3 条候选允许榜单 1 次＋单条详情 3 次，
第 5 次仍拒绝；默认 `batch50` 仍最多 2 次。运行前重新验证公开计划，非法配置不得先写日账。
这不是恢复金额限制，也不自动切换定时任务策略。

本地针对 Provider、黄金采集与省费规划回归：79 passed、1 skipped；跳过项依赖 PostgreSQL，
不能当作数据库集成通过。修正后的服务器存档重放、有效详情覆盖率和业务路由切换仍待验收。
不对同一个过滤样本反复付费重试。

### 合并后服务器存档重放

PR #108 已合并为 `4bd8cbdfee1a5a9e681a473a7b19ef3be16e9900`，两项
provider-tests CI 均通过（含 PostgreSQL）。服务器仓库快进到该版本，只复制源码到
500G 盘的独立检验目录，未发布 Windmill 脚本、页面或变更定时策略。

通过 PostgreSQL SELECT 将响应 #19 管道传入 Worker 中的新解析器；未读取 API Key、
未调用供应商、未写入业务表。依赖使用 uv 离线缓存；导入完成后将 Python socket 构造
替换为拒绝函数，再运行解析断言。输出 `STORED_DETAIL_REPLAY_VALID`，退出码 0，
`observations=0`。日志：
`/srv/douyin-research-test/evidence/stored-detail-replay-20260921-v2.log`。

首次探针在导入依赖前替换 socket，导致 psycopg/SSL 导入失败，未进入解析；原失败日志
`stored-detail-replay-20260921.log` 保留。调整的是探针初始化顺序，不是业务代码。
此次证明真实过滤响应不再产生假详情；不证明单条接口有效字段覆盖，也不代表运行脚本已部署。

## 采集输入与输出

- `plan_video_fetches(ids, purpose="detail", strategy="cost_aware")` 去重并校验 ID，返回纯计划，不访问网络。
- 详情按 1／10／50 容量与公开基础价组合；相同费用优先更少请求。3 条详情预计 $0.003，
  不再按未满的 50 条批次预计 $0.050。实际扣款仍由供应商账单确认。
- `purpose="statistics"` 仅取统计，按 2／50 容量规划。49 或 50 条优先一次 50 条统计请求。
- 不把统计冒充完整详情，不自动替换现有发现／详情流程。缺失字段保持缺失。
- 本地缓存命中无 HTTP 请求，保留原观察时间，不能把旧指标刷新成“刚刚采集”。
- 当前规划使用 2026-09-20 基础价；尚未将账户折扣、限速、字段覆盖率或延迟纳入自动优化。

## 费用记录与接口协作

| 事实 | 落库／展示 |
| --- | --- |
| 公开单价 × 已观察成功次数 | `estimated_cost`，`cost_basis=estimated_unit_price`；不是已扣款 |
| 本地缓存／明确免费接口／明确非计费 HTTP 错误 | 已知零费用，和未知费用区分 |
| 超时、价格缺失、SDK 内部尝试不可见 | 未知费用或已知估算小计＋未知尝试，不假装免费 |
| 供应商账单确认 | 只有 `supplier_bill` 且金额非空才展示“已对账”；本次未实现自动账单导入 |
| 旧 `verified_unit_price` | 当成历史报价；即使写在 `actual_cost` 也不自动升级为账单 |
| 不明来源的旧金额 | 来源未核验；USD 与 CNY 分开，不相加 |

每条 Provider 记录保存逻辑调用标识、HTTP 尝试数、未知尝试数、价格来源和价格版本。
Provider 记录数不等于 HTTP 请求次数。REST 默认可审计；SDK 只在显式兼容选项下启用，
尝试数保持未知。有请求预占 hook 时，禁止多次尝试或 SDK，保证一次预占最多发出一次请求。
自定义 transport 必须遵守同一单次尝试约定；不能用不透明重试 transport 接受此 hook。
未预占的 REST 重试仍逐次记入尝试日志，不将最终成功当成只有一次请求。

`daily_budget.spent_cost` 是**已知报价预占小计**，不是供应商账单。
未知价格另记 `unknown_price_requests`，只读 MCP 一并返回。
不配置金额上限时未知报价不阻断调用；调用者主动配置了金额上限时不能用未知报价绕过。
默认 CLI 金额上限为 `None`，本轮没有新增固定金额限制。

## 供应商账单边界

用户最新验收口径：**知道每天实际花多少钱即可**。优先展示供应商按天的账户实际费用，
不做逐笔请求对账或任务金额分摊；本地调用明细仅作诊断。币种分列，保留账单日期与供应商时区。

[TikHub 标准响应](https://api.tikhub.io/openapi.json)包含 `request_id` 与计费提示，
但标准 schema 未定义逐次扣费金额；不能把“将被计费”提示当金额。
[每日用量接口](https://docs.tikhub.io/186826051e0)可查询当前账户每日用量，
公开文档没有具体展开 `data` 字段。本次已通过后台免费接口查询核实真实返回：

- `data.date`：账单日期；响应 `time_zone` 为 `America/Los_Angeles`，不能当成本机北京时间自然日。
- `data.usage`：当日总费用；`balance_usage`：余额消费；`free_credit_usage`：免费额度使用量。
- `total_request_per_day`、`paid_request_per_day`：总请求／付费请求数；`uri_counts`：逐接口次数。
- 不保存或展示返回的账号邮箱；金额用 decimal 规范化，避免 JSON 浮点尾数。
- 当前接口无历史日期参数。轮询当前日会更新当日汇总；不能凭空声称能补拉所有历史天数。
- 后台使用日志支持日期／接口汇总和 CSV／JSON 导出，可作为历史账单来源。

后续直接按供应商日期、币种保存账户汇总；不得把账户汇总自动均摊回本项目请求，
尤其是账号可能还被其他程序使用时。endpoint 明细为可选排错信息，不作为日费用显示的前置条件。

本轮只实现账本口径和展示，没有实现自动账单同步、逐笔匹配或新的定时采集调度。
Golden 成功运行会写估算汇总；失败时应以 external_api_call 明细检查已有费用，
失败 pipeline_run 的默认费用字段不可当作“没有消费”的证明。

## 迁移、验收与切换

1. 数据库先应用 `db/migrations/014_unknown_price_reservations.sql`；可重复执行。
   历史未知报价无法倒推出，新列默认 0 只表示没有历史计数，不证明旧请求都已定价。
2. 部署对应后端和费用页，验证旧报价不显示为已对账，未知金额不显示为免费，币种分列。
3. 无密钥预览：`python scripts/tikhub/real_data_golden.py --max-items 3 --detail-strategy cost_aware`。
   应是 1 次榜单＋最多 3 次单条详情、零重试、不设固定金额上限；不发送请求。
4. 后续测试服显式小样本验收同 ID 的详情字段、统计字段与供应商扣款后，才切换业务默认路由。
   当前页面黄金入口仍沿用最多 2 次请求的旧 batch50 方案。

测试覆盖规划边界、真实 HTTP 请求形状（MockTransport）、缓存时间、失败／重试费用、
未知价格预占、MCP 查询与费用页聚合。隔离组件预览使用合成账本，不能冒充已部署页面或真实账单。
