import React, { useState } from 'react'
import { Alert, Button, Checkbox, Modal, Tag } from 'antd'
import { backend } from '../../backend'

type Candidate = {
  project_id: string
  video_id: string
  transcript_id: string
  review_version: string
  evidence_fingerprint: string
  evidence_bundle: {
    video_metadata?: { title?: string | null; description?: string | null; platform?: string | null }
    comment_features?: Record<string, unknown>
    transcript?: { text?: string; text_fingerprint?: string }
  }
}

// A new approval is required after the first live project task failed before
// Ark HTTP during evidence JSON serialization. Never replay that task key.
const reviewVersion = 'privacy-v2'

export default function ProjectL3ReviewPanel({ projectId, videoId, transcriptId }: {
  projectId: string
  videoId: string
  transcriptId: string
}) {
  const [candidate, setCandidate] = useState<Candidate | null>(null)
  const [reviewId, setReviewId] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [reviewOpen, setReviewOpen] = useState(false)

  const prepare = async () => {
    setBusy(true); setError(''); setNotice(''); setConfirmed(false); setCandidate(null)
    try {
      const result = await backend.project_prepare_l3_review({
        project_id: projectId, video_id: videoId, transcript_id: transcriptId,
        review_version: reviewVersion,
      }) as Candidate
      if (result.project_id !== projectId || result.video_id !== videoId || result.transcript_id !== transcriptId) {
        throw new Error('scope mismatch')
      }
      setCandidate(result)
      setReviewOpen(true)
    } catch {
      setError('项目 L3 待审内容不可用。请确认转写、评论统计和审核权限仍有效。')
    } finally { setBusy(false) }
  }

  const approve = async () => {
    if (!candidate || !confirmed || busy) return
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await backend.project_approve_l3_review({
        action: 'approve', project_id: projectId, video_id: videoId,
        transcript_id: transcriptId, review_version: candidate.review_version,
        evidence_fingerprint: candidate.evidence_fingerprint,
      }) as { status: string; review_id: string }
      if (result.status !== 'review_saved') throw new Error('not saved')
      setReviewId(result.review_id)
      setConfirmed(false)
      setNotice('本项目 L3 审核已保存；模型尚未调用，执行由独立后台任务控制。')
    } catch {
      setError('L3 审核未保存；待审正文可能已变化，请重新生成并核对。')
    } finally { setBusy(false) }
  }

  const revoke = async () => {
    if (!candidate || !reviewId || busy) return
    setBusy(true); setError(''); setNotice('')
    try {
      await backend.project_approve_l3_review({
        action: 'revoke', project_id: projectId, video_id: videoId,
        transcript_id: transcriptId, review_version: candidate.review_version,
        evidence_fingerprint: candidate.evidence_fingerprint, review_id: reviewId,
      })
      setReviewId(''); setConfirmed(false); setCandidate(null); setReviewOpen(false)
      setNotice('L3 审核已撤销；原审核不能再触发新模型调用。')
    } catch {
      setError('撤销未完成，请核对运行状态。')
    } finally { setBusy(false) }
  }

  const metadata = candidate?.evidence_bundle.video_metadata
  const transcript = candidate?.evidence_bundle.transcript
  const comments = candidate?.evidence_bundle.comment_features
  return <section className="detail-section" aria-label="项目 L3 正文审核">
    <div className="detail-section-head"><h4>本项目 L3 内容审核</h4>
      <Button loading={busy} disabled={Boolean(reviewId)} onClick={prepare}>生成待审正文</Button></div>
    <p className="muted">此处只核对将发送给模型的准确内容并保存审核，不直接发起付费调用。</p>
    {error && <Alert type="error" showIcon message={error} />}
    {notice && <Alert type="success" showIcon message={notice} />}
    {candidate && <p className="project-l3-review-summary">
      待审正文已生成，指纹 <code>{candidate.evidence_fingerprint.slice(0, 12)}…</code>。
      <Button type="link" onClick={() => setReviewOpen(true)}>打开完整正文核对</Button>
    </p>}
    <Modal
      title="本项目 L3 完整正文审核"
      open={reviewOpen && Boolean(candidate)}
      width={960}
      style={{ maxWidth: 'calc(100vw - 24px)' }}
      className="project-l3-review-dialog"
      footer={null}
      closable={!busy}
      maskClosable={!busy}
      keyboard={!busy}
      onCancel={() => { if (!busy) setReviewOpen(false) }}
    >
    {error && <Alert type="error" showIcon message={error} />}
    {notice && <Alert type="success" showIcon message={notice} />}
    {candidate && <div className="project-l3-review-body">
      <p><Tag>待审指纹</Tag><code>{candidate.evidence_fingerprint}</code></p>
      <h5>公开视频元数据</h5>
      <p>平台：{metadata?.platform || '未记录'} · 标题：{metadata?.title || '未记录'}</p>
      <p style={{ whiteSpace: 'pre-wrap' }}>{metadata?.description || '无描述'}</p>
      <h5>评论数字统计（不含原评论）</h5>
      <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 220, overflowY: 'auto' }}>
        {JSON.stringify(comments || {}, null, 2)}
      </pre>
      <h5>完整转写正文</h5>
      <div style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
        {transcript?.text || '无转写'}
      </div>
      <h5>精确发送内容（完整证据包）</h5>
      <p className="muted">上面的分组便于阅读；下面是本次审核指纹对应的全部字段，包含时间、时长、转写来源、项目绑定和审核版本。请以此核对实际发送范围。</p>
      <pre className="project-l3-review-exact-bundle">{JSON.stringify(candidate.evidence_bundle, null, 2)}</pre>
      <p className="muted">请核对个人信息与业务适用性；评论只发送统计特征，转写原文可能包含姓名。</p>
      {!reviewId ? <>
        <Checkbox checked={confirmed} disabled={busy} onChange={event => setConfirmed(event.target.checked)}>
          我已核对上方完整证据包，同意在本项目内交由云端模型分析
        </Checkbox>{' '}
        <Button type="primary" disabled={!confirmed || busy} onClick={approve}>保存 L3 审核</Button>
      </> : <Button danger disabled={busy} onClick={revoke}>撤销本次审核</Button>}
    </div>}
    </Modal>
  </section>
}
