import React, { useEffect, useRef, useState } from 'react'
import { Alert, Button, Input, Select, Tag } from 'antd'
import { backend } from './backend'

type Coverage = 'none' | 'partial' | 'complete'
type AudioCoverage = Coverage | 'not_applicable'
type Status = 'partial' | 'complete' | 'insufficient'
type CaseReview = {
  id: string
  version_no: number
  status: Status
  source_reference: string
  observed_at: string
  video_coverage: Coverage
  audio_coverage: AudioCoverage
  audio_not_applicable_reason?: string | null
  key_event_complete: boolean | null
  verified_facts: string
  evidence_gaps: string
  counterevidence: string
  comparability_note: string
  reviewed_by: string
  created_at: string
}

const emptyForm = () => ({
  status: 'partial' as Status,
  video_coverage: 'none' as Coverage,
  audio_coverage: 'none' as AudioCoverage,
  audio_not_applicable_reason: '',
  key_event_complete: null as boolean | null,
  verified_facts: '',
  evidence_gaps: '',
  counterevidence: '',
  comparability_note: '',
})

const statusText: Record<Status, string> = {
  partial: '部分核看', complete: '完整可分析', insufficient: '证据不足',
}

function originalPostUrl(platform: string, videoKey: string, sourceUrl?: string | null) {
  const hosts: Record<string, string[]> = {
    douyin: ['douyin.com', 'www.douyin.com'],
    kuaishou: ['kuaishou.com', 'www.kuaishou.com'],
    xiaohongshu: ['xiaohongshu.com', 'www.xiaohongshu.com'],
    bilibili: ['bilibili.com', 'www.bilibili.com', 'm.bilibili.com'],
    weibo: ['weibo.com', 'www.weibo.com'],
    wechat_channels: ['channels.weixin.qq.com'],
  }
  const encoded = encodeURIComponent(videoKey)
  const directPaths: Record<string, string[]> = {
    douyin: [`/video/${encoded}`],
    kuaishou: [`/short-video/${encoded}`],
    xiaohongshu: [`/explore/${encoded}`, `/discovery/item/${encoded}`],
    bilibili: [`/video/${encoded}`],
    weibo: [`/status/${encoded}`],
  }
  if (sourceUrl) {
    try {
      const parsed = new URL(sourceUrl)
      if (parsed.protocol === 'https:' && !parsed.port && !parsed.username && !parsed.password && hosts[platform]?.includes(parsed.hostname)
        && directPaths[platform]?.some(path => parsed.pathname === path || parsed.pathname === `${path}/`)) {
        parsed.search = ''; parsed.hash = ''
        return parsed.toString()
      }
    } catch { /* An invalid upstream source URL must not become a clickable link. */ }
  }
  if (platform === 'douyin') return `https://www.douyin.com/video/${encoded}`
  if (platform === 'kuaishou') return `https://www.kuaishou.com/short-video/${encoded}`
  if (platform === 'xiaohongshu') return `https://www.xiaohongshu.com/explore/${encoded}`
  if (platform === 'bilibili') return `https://www.bilibili.com/video/${encoded}`
  if (platform === 'weibo') return `https://www.weibo.com/status/${encoded}`
  return ''
}

export default function ProjectCaseReviewPanel({ projectId, videoId, platform, platformVideoId, sourceUrl, canWrite, isAccepted }: {
  projectId: string
  videoId: string
  platform: string
  platformVideoId: string
  sourceUrl?: string | null
  canWrite: boolean
  isAccepted: boolean
}) {
  const [review, setReview] = useState<CaseReview | null>(null)
  const [form, setForm] = useState(emptyForm)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const requestVersion = useRef(0)
  const submitAttempt = useRef<{ fingerprint: string; key: string; observedAt: string } | null>(null)
  const [sourceReference, setSourceReference] = useState(() => originalPostUrl(platform, platformVideoId, sourceUrl))

  const load = async () => {
    const version = ++requestVersion.current
    setLoading(true)
    setError('')
    try {
      const result = await backend.manage_project_case_review({
        project_id: projectId, video_id: videoId, action: 'latest',
      }) as { review: CaseReview | null }
      if (version === requestVersion.current) setReview(result.review)
    } catch (e) {
      if (version === requestVersion.current) setError(e instanceof Error ? e.message : String(e))
    } finally {
      if (version === requestVersion.current) setLoading(false)
    }
  }

  useEffect(() => {
    void load()
    return () => { requestVersion.current += 1 }
  }, [projectId, videoId])

  const save = async () => {
    if (busy || !canWrite || !isAccepted) return
    const version = ++requestVersion.current
    setBusy(true)
    setError('')
    setNotice('')
    const fingerprint = JSON.stringify({ form, sourceReference })
    if (!submitAttempt.current || submitAttempt.current.fingerprint !== fingerprint) {
      submitAttempt.current = { fingerprint, key: crypto.randomUUID(), observedAt: new Date().toISOString() }
    }
    const attempt = submitAttempt.current
    try {
      await backend.manage_project_case_review({
        project_id: projectId, video_id: videoId, action: 'create',
        idempotency_key: attempt.key,
        payload: {
          ...form,
          audio_not_applicable_reason: form.audio_coverage === 'not_applicable' ? form.audio_not_applicable_reason : null,
          source_reference: sourceReference,
          observed_at: attempt.observedAt,
        },
      })
      if (version !== requestVersion.current) return
      submitAttempt.current = null
      setForm(emptyForm())
      setNotice('案例核看记录已保存；这是独立于“项目纳入”的最新审核版本。')
      await load()
    } catch (e) {
      if (version === requestVersion.current) setError(`${e instanceof Error ? e.message : String(e)}；结果可能已保存，未修改表单时重试会复用同一提交键。`)
    } finally {
      setBusy(false)
    }
  }

  const completeInput = form.video_coverage === 'complete'
    && (form.audio_coverage === 'complete' || (form.audio_coverage === 'not_applicable' && form.audio_not_applicable_reason.trim().length >= 12))
    && form.key_event_complete === true && !!form.verified_facts.trim() && !!form.comparability_note.trim()

  return <section className="detail-section project-case-review" aria-label="本项目案例核看">
    <div className="detail-section-head"><h4>本项目案例核看</h4><span>{loading ? '读取中' : review ? `v${review.version_no} · ${statusText[review.status]}` : '尚无核看记录'}</span></div>
    <p className="muted">“已纳入”只表示本项目收录了公开视频；ASR/L3 也不能替代完整画面与声音核看。只有最新版本为“完整可分析”的案例才能进入新行动卡，单条完整案例仍不能证明通用规律。</p>
    {error ? <Alert type="error" showIcon message="案例核看未能保存或读取" description={error} /> : null}
    {notice ? <Alert type="success" showIcon message={notice} /> : null}
    {review ? <div className="project-case-review-summary">
      <Tag color={review.status === 'complete' ? 'green' : review.status === 'insufficient' ? 'red' : 'gold'}>{statusText[review.status]}</Tag>
      <span>审核人 {review.reviewed_by} · {new Date(review.created_at).toLocaleString('zh-CN')}</span>
      <p>核实事实：{review.verified_facts || '未记录'}</p>
      <p>证据缺口：{review.evidence_gaps || '未记录'}</p>
      <p>反证：{review.counterevidence || '未记录'}</p>
      <p>适用边界：{review.comparability_note || '未记录'}</p>
    </div> : null}
    {canWrite && isAccepted ? <div className="project-case-review-form">
      <label>核看原帖直链<Input aria-label="核看原帖直链" value={sourceReference} maxLength={512} placeholder="请输入与该视频一致的公开原帖 HTTPS 直链；移除分享参数" onChange={e => setSourceReference(e.target.value)} /></label>
      {sourceReference.startsWith('https://') ? <a href={sourceReference} target="_blank" rel="noopener noreferrer">打开原视频核看 ↗</a> : null}
      <label>核看结论<Select aria-label="案例核看结论" value={form.status} options={[
        { value: 'partial', label: '部分核看' }, { value: 'complete', label: '完整可分析' }, { value: 'insufficient', label: '证据不足' },
      ]} onChange={status => setForm({ ...form, status })} /></label>
      <label>画面覆盖<Select aria-label="画面覆盖" value={form.video_coverage} options={[
        { value: 'none', label: '未核看' }, { value: 'partial', label: '仅核看部分' }, { value: 'complete', label: '完整核看' },
      ]} onChange={video_coverage => setForm({ ...form, video_coverage })} /></label>
      <label>声音覆盖<Select aria-label="声音覆盖" value={form.audio_coverage} options={[
        { value: 'none', label: '未核听' }, { value: 'partial', label: '仅核听部分' }, { value: 'complete', label: '完整核听' }, { value: 'not_applicable', label: '确实不适用（须说明）' },
      ]} onChange={audio_coverage => setForm({ ...form, audio_coverage })} /></label>
      {form.audio_coverage === 'not_applicable' ? <label>声音不适用原因<Input aria-label="声音不适用原因" maxLength={500} value={form.audio_not_applicable_reason} placeholder="如原片确无声音且本研究问题不依赖音频；没听或听不到不能填不适用" onChange={e => setForm({ ...form, audio_not_applicable_reason: e.target.value })} /></label> : null}
      <label>关键事件是否完整入镜<Select aria-label="关键事件完整性" value={form.key_event_complete === null ? 'unknown' : form.key_event_complete ? 'yes' : 'no'} options={[
        { value: 'unknown', label: '尚未核实' }, { value: 'yes', label: '是，完整可见' }, { value: 'no', label: '否，存在缺失' },
      ]} onChange={value => setForm({ ...form, key_event_complete: value === 'unknown' ? null : value === 'yes' })} /></label>
      <label>画面/声音可核实事实<Input.TextArea aria-label="可核实事实" maxLength={3000} value={form.verified_facts} placeholder="写时间点、人物实际行动与可见结果；尚无可核实事实可留空，不要编造" onChange={e => setForm({ ...form, verified_facts: e.target.value })} /></label>
      <label>证据缺口<Input.TextArea aria-label="证据缺口" maxLength={2000} value={form.evidence_gaps} placeholder="未入镜、未核听、剪辑断点、无法确认的后果" onChange={e => setForm({ ...form, evidence_gaps: e.target.value })} /></label>
      <label>反证与替代解释<Input.TextArea aria-label="反证与替代解释" maxLength={2000} value={form.counterevidence} placeholder="例如观众只讨论游客才艺、角色互动可能排练" onChange={e => setForm({ ...form, counterevidence: e.target.value })} /></label>
      <label>与本项目的可比性<Input.TextArea aria-label="可比性与边界" maxLength={2000} value={form.comparability_note} placeholder="角色、形态、场地、受众、制作与权利哪里相同或不同" onChange={e => setForm({ ...form, comparability_note: e.target.value })} /></label>
      {form.status === 'complete' && !completeInput ? <Alert type="warning" showIcon message="完整案例仍缺画面/声音、关键事件、事实或可比性记录" /> : null}
      <Button type="primary" loading={busy} disabled={!sourceReference.trim() || (form.status === 'complete' && !completeInput)} onClick={() => void save()}>保存新核看版本</Button>
    </div> : <p className="muted">{!isAccepted ? '当前视频未被本项目接受；可查看历史核看，不能新增版本。' : '当前为只读成员；项目负责人、管理员或研究员可新增核看版本。'}</p>}
  </section>
}
