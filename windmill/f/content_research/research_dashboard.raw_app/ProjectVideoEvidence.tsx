import React, { useRef, useState } from 'react'
import { Alert, Button, Input, Popconfirm, Tag } from 'antd'
import { backend } from './backend'
import PlatformIcon from './src/components/PlatformIcon'
import { useProjectScope } from './src/projectScope'

type Candidate = {
  id: string
  platform: string
  platform_video_id: string
  title?: string | null
  account_name?: string | null
  published_at?: string | null
  project_status?: string | null
  play_count?: number | null
  like_count?: number | null
  comment_count?: number | null
}

export default function ProjectVideoEvidence({ projectId, onAccepted }: {
  projectId: string
  onAccepted: (videoId: string) => void
}) {
  const { projects } = useProjectScope()
  const role = projects.find((item) => item.id === projectId)?.member_role
  const [reference, setReference] = useState('')
  const [candidate, setCandidate] = useState<Candidate | null>(null)
  const [lookedUp, setLookedUp] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const requestVersion = useRef(0)

  if (role !== 'owner' && role !== 'admin') return null

  const lookup = async () => {
    if (!reference.trim()) return
    const version = ++requestVersion.current
    setBusy(true)
    setError('')
    setNotice('')
    setCandidate(null)
    setLookedUp(false)
    try {
      const result = await backend.manage_project_video_evidence({
        project_id: projectId, action: 'preview', video_reference: reference.trim(),
      }) as { found: boolean; video: Candidate | null }
      if (version !== requestVersion.current) return
      setCandidate(result.video)
      setLookedUp(true)
    } catch (e) {
      if (version === requestVersion.current) setError(e instanceof Error ? e.message : String(e))
    } finally {
      if (version === requestVersion.current) setBusy(false)
    }
  }

  const accept = async () => {
    if (!candidate || busy) return
    const version = ++requestVersion.current
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const result = await backend.manage_project_video_evidence({
        project_id: projectId, action: 'accept',
        video_reference: candidate.platform_video_id,
        idempotency_key: crypto.randomUUID(),
      }) as { status: string; video_id: string }
      if (version !== requestVersion.current) return
      setNotice(result.status === 'accepted' ? '已纳入本项目公开依据；项目共享仍需双方单独授权。' : '保存状态待核对。')
      setCandidate({ ...candidate, project_status: 'accepted' })
      onAccepted(result.video_id)
    } catch (e) {
      if (version === requestVersion.current) setError(e instanceof Error ? e.message : String(e))
    } finally {
      if (version === requestVersion.current) setBusy(false)
    }
  }

  const fmt = (value?: number | null) => value == null ? '—' : Number(value).toLocaleString('zh-CN')
  return <section className="card project-video-evidence" aria-label="纳入已有公开视频">
    <div className="project-video-evidence-head">
      <div><span className="project-video-evidence-eyebrow">项目公开依据</span><h2>纳入已有视频</h2></div>
      <Tag color="blue">仅本项目</Tag>
    </div>
    <p>粘贴抖音视频 ID 或完整直链，先核对公开资料，再确认纳入。这里只引用已采集的视频，不调用付费接口；未采集的视频请从“研究任务”发现。</p>
    <div className="project-video-evidence-form">
      <Input aria-label="抖音视频 ID 或直链" placeholder="视频 ID 或 https://www.douyin.com/video/…"
        value={reference} disabled={busy} onChange={(event) => {
          requestVersion.current += 1
          setReference(event.target.value)
          setCandidate(null)
          setLookedUp(false)
          setBusy(false)
          setError('')
          setNotice('')
        }} onPressEnter={() => void lookup()} />
      <Button onClick={() => void lookup()} loading={busy} disabled={!reference.trim()}>核对视频</Button>
    </div>
    {error ? <Alert type="error" showIcon message="视频未能核对或保存" description={error} /> : null}
    {notice ? <Alert type="success" showIcon message={notice} /> : null}
    {lookedUp && !candidate ? <div className="project-video-evidence-empty">现有公开库没有这条可用视频。可先用研究任务采集，不会自动把历史视频批量纳入项目。</div> : null}
    {candidate ? <div className="project-video-evidence-preview">
      <PlatformIcon platform={candidate.platform} />
      <div className="project-video-evidence-copy">
        <strong>{candidate.title || '未命名视频'}</strong>
        <span>{candidate.account_name || '未知账号'} · ID {candidate.platform_video_id}</span>
        <small>播放 {fmt(candidate.play_count)} · 点赞 {fmt(candidate.like_count)} · 评论 {fmt(candidate.comment_count)}</small>
      </div>
      <Popconfirm title="确认纳入本项目公开依据？" description="仅本项目可见；如需给其它项目看，还须双方单独授权。"
        onConfirm={() => void accept()}>
        <Button type="primary" loading={busy} disabled={candidate.project_status === 'accepted'}>
          {candidate.project_status === 'accepted' ? '已纳入' : '确认纳入'}
        </Button>
      </Popconfirm>
    </div> : null}
  </section>
}
