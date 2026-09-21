import React, { useEffect, useState } from 'react'
import { Alert, Button, Checkbox, Input, Tag } from 'antd'
import { backend } from '../../backend'

type ReviewManifest = {
  status?: string
  video_id?: string
  evidence_fingerprint?: string
  evidence_version?: string
  evidence_modalities?: string[]
  privacy_review_version?: string
  generated_at?: string
  source_summary?: Record<string, unknown>
}

type BudgetPreview = {
  status?: string
  paid_execution_available?: boolean
  maximum_external_calls?: number
  maximum_llm_calls?: number
  adapter_status?: string
  eligibility?: Record<string, unknown>
  planned_model?: {
    provider?: string
    model_id?: string
    model_revision?: string
    prompt_version?: string
    pricing_version?: string
  }
  budget?: {
    budget_key?: string
    date?: string
    configured?: boolean | null
    max_cost?: number | null
    spent_cost?: number | null
    max_requests?: number | null
    used_requests?: number | null
    remaining_cost?: number | null
    remaining_requests?: number | null
    currency?: string | null
  }
  estimated_llm_cost?: number | null
  remaining_after_estimate?: number | null
  cost_currency?: string | null
}

const reviewVersionDefault = 'privacy-v1'

function uuid() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  return `l3-review-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function valueText(value: unknown) {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'boolean') return value ? '通过' : '未通过'
  return String(value)
}

function safeErrorText(error: unknown, fallback: string) {
  const message = error instanceof Error ? error.message : String(error)
  const known: Record<string, string> = {
    L3_REVIEW_CANDIDATE_UNAVAILABLE: '当前证据候选不可用，请确认 L2 证据完整后重试。',
    L3_REVIEW_CANDIDATE_STALE: '候选证据已变化，请重新生成待审摘要。',
    L3_REVIEW_IDEMPOTENCY_CONFLICT: '该审核请求与先前请求冲突，请重新生成待审摘要。',
    L3_REVIEW_INPUT_INVALID: '审核请求字段无效，请重新生成待审摘要。',
    L3_REVIEW_APPROVAL_FAILED: '审核记录未保存，请稍后重试。',
    L3_BUDGET_PREVIEW_CONFIG_INVALID: '服务端预算预览配置无效，请联系管理员。',
    L3_BUDGET_PREVIEW_FAILED: '预算预览暂不可用，请稍后重试。',
  }
  const code = Object.keys(known).find((item) => message.includes(item))
  return code ? known[code] : fallback
}

export default function L3ReviewPanel({
  videoId,
  researchLevel,
}: {
  videoId: string
  researchLevel: number
}) {
  const [reviewVersion, setReviewVersion] = useState(reviewVersionDefault)
  const [manifest, setManifest] = useState<ReviewManifest | null>(null)
  const [approvalIdempotencyKey, setApprovalIdempotencyKey] = useState('')
  const [budget, setBudget] = useState<BudgetPreview | null>(null)
  const [reviewConfirmed, setReviewConfirmed] = useState(false)
  const [busy, setBusy] = useState<'prepare' | 'approve' | 'preview' | ''>('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => {
    setManifest(null)
    setApprovalIdempotencyKey('')
    setBudget(null)
    setReviewConfirmed(false)
    setError('')
    setNotice('')
  }, [videoId])

  const prepare = async () => {
    setBusy('prepare')
    setError('')
    setNotice('')
    try {
      const result = await backend.prepare_l3_review({
        video_id: videoId,
        privacy_review_version: reviewVersion.trim(),
      }) as ReviewManifest
      setManifest(result)
      setApprovalIdempotencyKey(uuid())
      setReviewConfirmed(false)
      setNotice('已生成待审摘要。请在受控审核渠道查看正文后再确认。')
    } catch (e) {
      setError(safeErrorText(e, '当前证据候选不可用，请稍后重试。'))
    } finally {
      setBusy('')
    }
  }

  const approve = async () => {
    if (!manifest?.evidence_fingerprint || !reviewConfirmed || !approvalIdempotencyKey) return
    setBusy('approve')
    setError('')
    setNotice('')
    try {
      const result = await backend.approve_l3_review({
        video_id: videoId,
        privacy_review_version: manifest.privacy_review_version || reviewVersion.trim(),
        evidence_fingerprint: manifest.evidence_fingerprint,
        evidence_version: manifest.evidence_version,
        evidence_modalities: manifest.evidence_modalities || [],
        idempotency_key: approvalIdempotencyKey,
      }) as { status?: string }
      setNotice(result.status === 'review_saved' ? '审核记录已保存。' : '审核请求已完成。')
      setReviewConfirmed(false)
    } catch (e) {
      setError(safeErrorText(e, '审核记录未保存，请稍后重试。'))
    } finally {
      setBusy('')
    }
  }

  const previewBudget = async () => {
    if (!manifest?.evidence_fingerprint) return
    setBusy('preview')
    setError('')
    try {
      const result = await backend.preview_l3_budget({
        video_id: videoId,
        privacy_review_version: manifest.privacy_review_version || reviewVersion.trim(),
        evidence_fingerprint: manifest.evidence_fingerprint,
      }) as BudgetPreview
      setBudget(result)
    } catch (e) {
      setError(safeErrorText(e, '预算预览暂不可用，请稍后重试。'))
    } finally {
      setBusy('')
    }
  }

  return (
    <section className="detail-section l3-review-section">
      <div className="detail-section-head">
        <h4>L3 审核与预算预览</h4>
        <Tag color="gold">仅预览</Tag>
      </div>
      <p className="l3-review-copy">此面板用于保存审核与预览预算，不执行模型调用或预占预算。实际分析由独立任务执行，已完成结果在上方展示。</p>

      {error && <Alert className="l3-review-alert" type="error" showIcon message="L3 审核流程未完成" description={error} />}
      {notice && <Alert className="l3-review-alert" type="success" showIcon message={notice} />}

      <div className="l3-review-controls">
        <label>
          <span>审核版本</span>
          <Input value={reviewVersion} onChange={(event) => setReviewVersion(event.target.value)} disabled={!!manifest || !!busy} />
        </label>
        <Button type="primary" loading={busy === 'prepare'} disabled={researchLevel < 2 || !!busy || !reviewVersion.trim()} onClick={prepare}>
          生成待审摘要
        </Button>
      </div>
      {researchLevel < 2 && <p className="l3-review-muted">仅已进入 L2 的视频可以生成 L3 待审证据摘要。</p>}

      {manifest && (
        <div className="l3-manifest">
          <div className="l3-manifest-fingerprint"><span>审核对象 ID</span><code>{videoId}</code></div>
          <div className="l3-manifest-row"><span>证据版本</span><b>{valueText(manifest.evidence_version)}</b></div>
          <div className="l3-manifest-row"><span>审核版本</span><b>{valueText(manifest.privacy_review_version)}</b></div>
          <div className="l3-manifest-row"><span>证据摘要</span><b>{(manifest.evidence_modalities || []).join(' / ') || '—'}</b></div>
          <div className="l3-manifest-fingerprint"><span>SHA-256 指纹</span><code>{manifest.evidence_fingerprint}</code></div>
          <Alert
            className="l3-review-alert"
            type="warning"
            showIcon
            message="本 L3 审核面板只显示脱敏摘要"
            description="本 L3 审核候选的完整正文必须在受控审核渠道核对；此 L3 审核面板和审核任务结果仅展示摘要与指纹。已完成的转写可在独立转写面板查看。"
          />
          <Checkbox checked={reviewConfirmed} onChange={(event) => setReviewConfirmed(event.target.checked)} disabled={!!busy}>
            我已在受控渠道完成该指纹对应正文的隐私审核，并确认保存此审核记录。
          </Checkbox>
          <Button type="primary" loading={busy === 'approve'} disabled={!reviewConfirmed || !!busy} onClick={approve}>
            保存审核记录
          </Button>
        </div>
      )}

      <div className="l3-budget-preview">
        <div className="l3-budget-head"><strong>预算预览</strong><span>服务端核验配置快照，非报价、非预占</span></div>
        <div className="l3-review-controls">
          <p className="l3-review-muted">预算预览将校验当前待审指纹和服务端模型/价格配置。</p>
          <Button loading={busy === 'preview'} disabled={!!busy || !manifest?.evidence_fingerprint} onClick={previewBudget}>查看预算预览</Button>
        </div>
        {budget && (
          <div className="l3-budget-grid">
            <span>预览状态 <b>{valueText(budget.status)}</b></span>
            <span>计划模型 <b>{[budget.planned_model?.provider, budget.planned_model?.model_id].filter(Boolean).join(' / ') || '—'}</b></span>
            <span>模型修订 <b>{valueText(budget.planned_model?.model_revision)}</b></span>
            <span>定价版本 <b>{valueText(budget.planned_model?.pricing_version)}</b></span>
            <span>服务端 LLM 估算 <b>{valueText(budget.estimated_llm_cost)} {budget.cost_currency || ''}</b></span>
            <span>预算已配置 <b>{valueText(budget.budget?.configured)}</b></span>
            <span>剩余成本 <b>{valueText(budget.budget?.remaining_cost)} {budget.budget?.currency || budget.cost_currency || ''}</b></span>
            <span>剩余次数 <b>{valueText(budget.budget?.remaining_requests)}</b></span>
            <span>估算后余额 <b>{valueText(budget.remaining_after_estimate)}</b></span>
            <span>外部调用数 <b>{valueText(budget.maximum_external_calls)}</b></span>
            <span>付费执行 <b>false（不可用）</b></span>
          </div>
        )}
        <p className="l3-review-muted">{!manifest ? '请先生成当前待审摘要，才能预览预算。' : 'paid_execution_available=false：本页没有执行入口；预算数据仅供人工决策，不代表供应商报价或任何预算锁定。'}</p>
      </div>
    </section>
  )
}
