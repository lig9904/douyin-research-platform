# Windmill 研究台安全写操作 V1

日期：2026-09-20

## 范围

研究台允许已登录成员执行四类内部写操作：

- 视频、账号、热点加入监测；
- 视频、账号、热点加入用户专题；
- 视频加入个人“我的收藏”；
- 保存并重新应用视频库筛选。

这些操作只更新 `douyin_research` 内部研究状态，不调用 TikHub、ASR、LLM，
不修改外部平台内容，也不产生 Provider 费用。

## 输入与身份

前端只提交资产 UUID、动作、内部状态、专题/筛选名称和 UUID v4 幂等键。
调用方不能提交 actor；后端只从 Windmill `WM_END_USER_EMAIL` 获取并校验当前用户。
写入还必须命中服务器管理的 `f/content_research/research_action_writers` allowlist：该变量可用
小写 email 的 JSON 数组或逗号/换行列表配置，但值只存在本机或目标环境，绝不进入 Git。变量缺失、
格式错误、身份缺失或不在名单内时，后端在建立数据库连接前失败关闭。

`monitoring_status`、`monitoring_priority` 与 `next_due_at` 是团队共享研究队列，
不是个人偏好；任何 allowlist 中的研究成员都能看到并维护同一状态。actor 用于来源校验、
幂等绑定和审计。专题、收藏与保存筛选才按 actor 隔离。只读 Viewer 即使可打开 Members-mode
App，也会在后端被拒绝，不能只依赖 Folder ACL 或前端隐藏按钮；测试服务器仍须同时验证真实
admin/reviewer/viewer 的 Folder ACL。

单次批量操作最多 50 个唯一资产；专题/筛选名最多 80 字符，备注最多 500 字符，
保存筛选的规范 JSON 最大 4096 字节并使用按页面固定的字段白名单。筛选值不允许嵌套对象
或数组，避免把任意正文、Secret 或 Provider 响应伪装成筛选保存。

## 接口与持久化

- `mutate_research_state`：监测、专题、收藏、保存筛选；
- `get_research_user_state`：只返回当前用户自己的专题计数和保存筛选；
- `research_user_action`：actor + action + payload SHA-256 + 聚合结果的幂等审计；
- `saved_research_filter`：当前用户的页面筛选；
- `collection` / `collection_item`：支持视频、账号、热点三类资产。

相同幂等键和完全相同输入只返回已有聚合结果，不重复写入。相同键绑定不同 actor、动作
或输入时拒绝；业务写入、审计和结果在同一 PostgreSQL 事务内完成，错误时整体回滚。
输出不返回 actor、数据库连接、内部审计 ID 或资产正文。

## 页面协作

视频库支持批量专题、详情监测、专题、收藏，以及保存/应用筛选；账号库和热点库支持批量
监测、详情监测和专题。页面成功后重新读取规范数据库结果，不用前端乐观状态冒充保存成功。

## 验收

- 身份缺失零写入；
- allowlist 未配置、格式错误或 Viewer 身份零写入；
- 不存在资产零写入，审计记录也回滚；
- 视频、账号、热点均能加入同一专题；
- 监测更新和保存筛选可在数据库重读；
- 两个获准写入的研究成员共享监测队列，但专题和筛选互相不可见；
- 幂等重放 `db_writes=0`，冲突键失败关闭；
- 所有操作 `external_calls=0`、`llm_calls=0`；
- 浏览器实际点击后刷新页面仍能看到保存状态。

测试服务器仍需用真实 admin/reviewer/viewer 账号验证 Folder ACL；本模块不替代目标环境的
身份与权限现场验收。
