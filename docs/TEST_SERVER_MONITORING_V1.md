# 测试服务器监控与告警契约 V1

这是一份只在本机和 CI 校验的监控/告警**契约**，不是已接入真实告警平台的声明。它不保存 URL、邮箱、Webhook、凭据、请求标识或告警正文；也不会发起 Provider 调用、HTTP 投递、邮件或其他外呼。

固定监测对象为：`proxy_https`、`windmill_health`、`postgres_health`、`backup_failure`、`restore_failure`。每次运行必须使用 `Asia/Shanghai` 时区、禁用 Provider 外呼、固定幂等键、最大并发 1、1--300 秒超时，以及 `disable_and_alert` 失败策略。

## 本机契约文件

在一个受限权限目录创建模板：

```bash
scripts/test-server-monitoring-contract.py init \
  --output "$PWD/monitoring-contract.json"
scripts/test-server-monitoring-contract.py verify \
  --input "$PWD/monitoring-contract.json"
```

工具只接受绝对路径、常规非符号链接文件，且输入和输出必须精确为 `0600`；单个文件上限 1 MiB，任何已有目标都不会覆盖。验证与报错都不会回显契约正文或被拒绝的敏感值。

JSON Schema 用于结构和单对象状态约束，Python 校验器是包含跨对象关系、时间顺序和敏感值扫描的最终权威；两者都必须通过。这里固定的是安全调度边界，不是目标环境的探测阈值、通知路由或值班策略；这些参数仍须在测试服务器信息明确后单独配置和验收。

## 状态机和验收

每个对象都有独立告警状态：`not_run → fired → acknowledged → closed`。`fired` 需要触发 UTC 时间；`acknowledged` 还需要确认时间；`closed` 需要三项严格递增的 UTC 时间。失败的监测不能保持 `not_run` 告警；尚未执行或被阻断的监测不能伪造告警流转。

所有五个监测对象都已执行，且未留下进行中的告警时，才可执行完整本地验收：

```bash
scripts/test-server-monitoring-contract.py verify \
  --input "$PWD/monitoring-contract.json" \
  --require-complete
scripts/test-server-monitoring-contract.py seal \
  --input "$PWD/monitoring-contract.json" \
  --output "$PWD/monitoring-contract.sha256" \
  --require-complete
```

`seal` 只生成独立 SHA-256 边车文件，不能把本机状态机校验解释为真实告警已投递、已被外部人员接收或已经形成生产监控覆盖。真实通知渠道、收件人、升级规则和演练记录必须在后续目标环境中另行授权、配置和验收。
