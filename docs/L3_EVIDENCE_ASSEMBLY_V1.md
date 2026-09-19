# L3 证据组装边界 V1

日期：2026-09-19
证据版本：`l3-evidence-v1.0.0`

## 目标

本模块从 PostgreSQL 中已有的 L2 数据构造一次 L3 模型调用的确定性输入。它只读数据库，不访问 TikHub、ASR、LLM 或其他外部服务，不读取密钥，也不持久化证据包。

返回值包含内存中的证据包和该证据包规范 JSON 的 SHA-256。调用方必须把这个指纹同时用于 `L3ExecutionRequest.input_fingerprint`，从而把执行任务、模型响应和实际输入绑定起来。

## 准入条件

组装前必须同时满足：

1. 调用方明确传入 `privacy_reviewed=true` 和非空、格式受限的隐私复核版本；该格式检查在连接数据库之前完成。
2. 最新一条 `human_annotation(annotation_type='l3_privacy_review')` 必须由非空 actor 写入，且其 `reviewed=true`、版本、证据版本、modalities 和证据 SHA-256 与本次候选快照完全一致。
3. 视频存在且 `research_level >= 2`。
4. 视频已经被 L2→L3 闸门以 `selected` 结果选中。
5. 存在评论特征快照。
6. 存在转写，并且最新转写状态为 `usable`、`low_confidence` 或 `no_speech`。

系统不会回退到较旧的可用转写。如果最新转写为 `unreviewed` 或 `rejected`，组装失败关闭，避免把已经被复核否定的新证据替换成旧证据。

## 输入内容

证据包固定包含三类 modality：

- `metadata`：平台、标题、描述、发布时间、时长、可用状态。
- `comments`：最新 `comment-features-v1.x` 的聚合计数与分布值。
- `transcript`：最新合格转写的文本、质量状态和模型/引擎版本。

NULL 保持 NULL，明确的 0 保持 0。时间统一序列化为 UTC，数值转换为 JSON 数值，字段按规范 JSON 排序后计算指纹。相同数据库证据与复核版本会得到相同指纹；任一实际模型输入变化都会改变指纹。

## 隐私与最小化

证据包不包含：

- canonical video UUID、平台视频 ID、账号 ID、昵称或 source URL
- 评论 ID、评论原文、请求 ID、cursor、raw reference
- 转写来源指纹、任务 ID 或任意数据库 metadata JSON
- L2→L3 批次 ID 和内部运行 ID

评论 snapshot 的 `metadata` 不会整体透传；组装器只输出固定的范围声明。转写正文和视频标题/描述仍可能含个人信息，所以 `privacy_reviewed=true` 只是请求方选择的复核版本；真正的准入事实来自最新持久化人工复核记录及其中绑定的证据 SHA-256。本模块仍不声称能自动识别个人信息。审核后任一标题、说明、评论快照或转写变化都会产生新指纹，并要求重新审核。

标题、描述、转写和最终序列化字节数都有硬上限。超限时拒绝，不静默截断证据。

## 协作接口

```python
candidate = L3EvidenceAssembler(dsn).prepare_for_privacy_review(
    video_id,
    privacy_review_version="privacy-v1",
)

# 人工检查 candidate.evidence_bundle 后，持久化 reviewed、actor、version、
# candidate.input_fingerprint、candidate.evidence_version 和 modalities。

assembled = L3EvidenceAssembler(dsn).assemble(
    video_id,
    privacy_reviewed=True,
    privacy_review_version="privacy-v1",
)

request = L3ExecutionRequest(
    ...,
    input_fingerprint=assembled.input_fingerprint,
)

coordinator.run(
    request,
    evidence_factory=lambda: assembled,
    provider_factory=provider_factory,
)
```

`prepare_for_privacy_review()` 只读数据库并生成待审候选，不授予执行权限。正式执行的 `evidence_factory` 必须返回已经由精确指纹审核通过的 `L3EvidenceBundle`。执行控制层会重新规范化证据 JSON、计算 SHA-256，并在构造 Provider 和预占预算之前核对版本、modalities、隐私复核版本、证据视频 ID 与请求视频 ID。它还会重新读取最新持久化人工复核记录并匹配证据指纹；普通 Mapping、伪造 dataclass 字段、陈旧审核或组装后的内存篡改都不能绕过该绑定。

正式付费执行仍由 L3 执行控制层负责预算、确认、单并发、零重试和故障对账；证据组装器不扩大任何执行授权。

## 验收边界

- 单元/集成测试证明：准入失败关闭、持久化人工复核与精确快照绑定、陈旧审核拒绝、选择最新快照、NULL/0 保真、指纹重算、敏感标识不透传、超限拒绝和执行数据库零写入。
- 这些测试不证明自动隐私识别，也不证明真实模型内容安全。
- 未经单独授权，不发起付费模型调用，不把本地实现视为生产启用。
