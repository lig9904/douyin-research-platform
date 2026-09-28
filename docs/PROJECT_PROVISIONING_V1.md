# 业务项目初始化（受控运维入口）

研究台当前只列出用户已有成员身份的项目，不在普通 Raw App 中开放“创建组织/项目”。项目成员、共享授权和公开依据仍在项目协作页管理。新业务项目必须先由数据库运维人员按确切归属创建，避免任何 App Reader 自行生成组织、授予 Owner 或把验收项目改名复用。

当前只提供代码与预览方案；用户明确暂不创建“九九 IP 号”项目。以下 `--apply` 是未来获准后的操作示例，本轮不得执行。

`scripts/provision-research-project.py` 只负责一个组织、一个项目和首位 Owner 的原子创建，使用 Python 3.14 与 `psycopg`。连接从 `RESEARCH_PROVISION_DSN` 环境变量读取，绝不把密码放进命令行参数或输出。默认仅预览；写入时必须同时传 `--apply`、匹配的 `--expected-db` 和准确的 `--confirm organization-slug/project-slug`。默认项目状态 `draft`，不会出现在业务研究范围，也不会自动采集或调用付费接口；首次明确指定 `--project-status active` 才创建可用项目。既有项目不改名、不激活、不补 Owner，而是拒绝重复创建。

运行前核对：目标为测试或已批准的环境；组织/项目的名称与归属已由业务负责人确认；Owner 邮箱是实际 Windmill 登录身份；数据库已有项目/成员/用户动作表；运维操作者邮箱与连接来源可信。`--operator-email` 是运维人员**声明的**审计身份，不是 CLI 独立认证；数据库访问权限和主机操作日志仍是控制点。

预览与执行使用同一组业务参数，例如：

```bash
python3.14 scripts/provision-research-project.py \
  --expected-db douyin_research \
  --organization-slug example-organization --organization-name '示例组织' \
  --project-slug example-project --project-name '示例业务项目' \
  --operator-email operator@example.com --owner-email owner@example.com

python3.14 scripts/provision-research-project.py \
  --expected-db douyin_research \
  --organization-slug example-organization --organization-name '示例组织' \
  --project-slug example-project --project-name '示例业务项目' \
  --operator-email operator@example.com --owner-email owner@example.com \
  --project-status active --apply --confirm example-organization/example-project
```

上面的地址与名称都是示例，不能直接用于渔岛。预览返回 `mode=preview`；执行返回 `mode=applied`、项目 ID 与 `research_user_action` 审计 ID。执行后用 Owner 身份重新打开研究台，确认仅出现该项目、主体初始为空、没有自动新增任务或共享；再由项目负责人维护成员和主体。数据库直写不替代这个页面验收。若组织已存在但名称或状态不同、项目已存在、数据库名不符或确认目标不匹配，命令拒绝写入。用户账号仍由 Windmill 创建，本工具不会创建账号或设置密码。
