# Data Provenance & Provider Exit V1

日期：2026-09-19

## 背景

TikHub 当前 Terms of Use 明确：
- 客户对 API 数据的存储、处理和再使用承担责任；
- Supported Platform / endpoint / availability 可能变化；
- 服务终止后，条款要求停止使用服务，并删除通过服务获得的 cached/stored data。

这意味着 V1 不能把“研究资产”和“TikHub 原始数据”混成不可分离的一份数据库。

本文只解决技术可清退性，不替代法律/合规判断。

## 数据分层

### A. Provider Data

可按 provider 清退：
- external_api_response
- provider_video_snapshot
- provider_account_snapshot
- provider-sourced metric_snapshot
- provider-sourced comments
- provider-sourced signals/snapshots
- provider media/transcript（若来源为该 provider）

### B. Canonical Identity

- source_video
- source_account

它们是系统内部 canonical entity。

每个 canonical entity 必须能追溯“由哪些 provider / direct sources 观察到”。

如果某 provider 被清退：
- 有其他合法 source 支持 → canonical entity 保留
- 仅由被清退 provider 支持 → 进入 provider-exit review，不默认静默保留来源数据

### C. Internal Assets

单独存：
- human_annotation
- collection
- rule-derived scores
- internally authored research notes
- Pattern / hypotheses
- reports

这些不能把 provider raw payload 复制进 output。

如果未来条款/合规要求影响其保留，应由独立策略决定；技术上必须能与 Provider Data 分开处理。

## Provider Entity Lineage

需要记录：

```text
provider
entity_type
entity_id
first_observed_at
last_observed_at
observation_count
source_mode
```

source_mode 示例：
- api
- manual_url
- other_provider
- direct_platform

## Purge Workflow

```text
provider = tikhub
↓
dry-run report
↓
列出：
  raw API responses
  provider snapshots
  metrics/comments/signals
  affected canonical videos/accounts
  affected analyses/transcripts
↓
确认
↓
删除 provider-owned records
↓
re-evaluate canonical entities
↓
orphan entity -> mark/tombstone/review
↓
rebuild search index/materialized views
↓
输出 purge report
```

V1 必须先支持 dry-run，不自动执行不可逆 purge。

## Provider-neutral Analysis Rule

任何长期研究结论不应把整段 provider raw JSON 嵌入 analysis output。

保存：
- case/entity ID
- evidence refs
- extracted minimal facts
- internal conclusion

原始证据仍通过 lineage 指向 Provider Data。

## UI

Case 详情技术信息区显示：
- Data source
- Last captured
- Provider(s)
- Provider state

普通用户不需要看到复杂 lineage，但 Admin 必须能查。

## Vendor Exit Test

V1.1 前至少做一次“假清退”测试：

```text
begin transaction
simulate provider purge
validate:
  app still starts
  internal annotations remain referentially valid
  no dangling FK
  provider raw data is removable
rollback
```

## Current TikHub Terms Source

https://docs.tikhub.io/9453286m0
