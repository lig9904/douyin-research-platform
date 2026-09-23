import React, { useEffect, useRef, useState } from 'react'
import { Alert, Button, Input, Popconfirm, Select, Spin, Tag } from 'antd'
import { backend } from './backend'
import AppShell, { type ResearchView } from './AppShell'
import PlatformIcon from './src/components/PlatformIcon'
import type { ProjectScope } from './src/projectScope'
import './project-collaboration.css'

type Member = {
  actor_id: string
  role: string
  status: string
  effective_until?: string | null
}
type Grant = {
  id: string
  other_project_name: string
  status: string
  effective_until: string
}
type SharedVideo = {
  source_project_id: string
  source_project_name: string
  video_id: string
  platform: string
  title?: string | null
  account_name?: string | null
  play_count?: number | null
  like_count?: number | null
  comment_count?: number | null
}
type Collaboration = {
  project_id: string
  project_name: string
  role: string
  members: Member[]
  eligible_targets: { id: string; name: string }[]
  outgoing: Grant[]
  incoming: Grant[]
  shared_videos: SharedVideo[]
}

const roleNames: Record<string, string> = {
  owner: '负责人', admin: '管理员', researcher: '研究员',
  analyst: '分析员', viewer: '只读成员',
}
const roleColors: Record<string, string> = {
  owner: 'blue', admin: 'cyan', researcher: 'geekblue', analyst: 'purple', viewer: 'default',
}
const actionMessages: Record<string, string> = {
  member_set: '成员权限已保存，项目接口会按新角色判权。',
  member_revoke: '成员访问已撤销。',
  share_offer: '邀请已发出；接收项目确认前不会开放证据。',
  share_accept: '共享已接受；仅公开证据对本项目成员可见。',
  share_decline: '邀请已拒绝。',
  share_revoke: '共享已停止。',
}
function dateLabel(value?: string | null) {
  if (!value) return '长期有效'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '日期待核对' : date.toLocaleDateString('zh-CN')
}

export default function ProjectCollaboration({ scope, onNavigate }: {
  scope: ProjectScope
  onNavigate: (view: ResearchView) => void
}) {
  const projectId = scope.mode === 'project' ? scope.projectId : null
  const currentProject = useRef(projectId)
  currentProject.current = projectId
  const requestVersion = useRef(0)
  const mutationVersion = useRef(0)
  const [data, setData] = useState<Collaboration | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [noticeWarning, setNoticeWarning] = useState(false)
  const [email, setEmail] = useState('')
  const [role, setRole] = useState('viewer')
  const [target, setTarget] = useState<string | undefined>()
  const [days, setDays] = useState(30)

  const refresh = async () => {
    if (!projectId) return false
    const version = ++requestVersion.current
    setLoading(true)
    setError('')
    try {
      const result = await backend.get_project_collaboration({ project_id: projectId }) as Collaboration
      if (version === requestVersion.current && currentProject.current === projectId) {
        setData(result)
        return true
      }
    } catch (e) {
      if (version === requestVersion.current && currentProject.current === projectId) {
        setData(null)
        setError(e instanceof Error ? e.message : String(e))
      }
    } finally {
      if (version === requestVersion.current && currentProject.current === projectId) setLoading(false)
    }
    return false
  }

  useEffect(() => {
    setData(null)
    setError('')
    setNotice('')
    setNoticeWarning(false)
    setEmail('')
    setTarget(undefined)
    setBusy(false)
    mutationVersion.current += 1
    void refresh()
    return () => { requestVersion.current += 1; mutationVersion.current += 1 }
  }, [projectId])

  const mutate = async (action: string, extra: Record<string, unknown>) => {
    if (!projectId || currentProject.current !== projectId || busy) return
    const version = ++mutationVersion.current
    setBusy(true)
    setError('')
    setNotice('')
    setNoticeWarning(false)
    try {
      await backend.mutate_project_collaboration({ project_id: projectId, action, ...extra })
      if (currentProject.current !== projectId || version !== mutationVersion.current) return
      if (action === 'member_set') setEmail('')
      if (action === 'share_offer') setTarget(undefined)
      const refreshed = await refresh()
      if (currentProject.current === projectId && version === mutationVersion.current) {
        setNotice(refreshed ? actionMessages[action] || '操作已保存。' : '操作已提交，但最新状态未核验；请刷新权限核对。')
        setNoticeWarning(!refreshed)
      }
    } catch (e) {
      if (currentProject.current === projectId && version === mutationVersion.current) setError(e instanceof Error ? e.message : String(e))
    } finally {
      if (version === mutationVersion.current) setBusy(false)
    }
  }

  if (!projectId) return null
  if (data && data.project_id !== projectId) return <AppShell activeView="collaboration" onNavigate={onNavigate}
    title="项目协作" subtitle="正在核验当前项目的权限"><div className="project-collaboration-loading"><Spin /></div></AppShell>
  const canManage = data?.role === 'owner' || data?.role === 'admin'
  const existingMembers = data?.members.filter((member) => member.status === 'active' &&
    (!member.effective_until || new Date(member.effective_until).getTime() > Date.now())) || []
  const ownerCount = existingMembers.filter((member) => member.role === 'owner').length
  const pendingIncoming = data?.incoming.filter((grant) => grant.status === 'offered').length || 0
  const activeShares = (data?.outgoing.filter((grant) => grant.status === 'active').length || 0) +
    (data?.incoming.filter((grant) => grant.status === 'active').length || 0)
  const fmt = (value: number | null | undefined) => value == null ? '—' : Number(value).toLocaleString('zh-CN')

  return <AppShell activeView="collaboration" onNavigate={onNavigate}
    title="项目协作" subtitle="把成员权限和项目间公开证据共享分开管理"
    actions={<Button onClick={() => void refresh()} disabled={loading || busy}>刷新权限</Button>}
    mainClassName="project-collaboration-page">
    <div className="project-collaboration-content">
      {error ? <Alert type="error" showIcon message="协作数据或操作未能核验" description={error} /> : null}
      {notice ? <Alert type={noticeWarning ? 'warning' : 'success'} showIcon closable onClose={() => setNotice('')} message={notice} /> : null}
      {loading && !data ? <div className="project-collaboration-loading"><Spin tip="正在核验项目权限" /></div> : null}
      {data ? <>
        <section className="card project-collaboration-intro" aria-label="当前项目协作概况">
          <div>
            <span className="project-collaboration-eyebrow">当前协作范围</span>
            <h2>{data.project_name}</h2>
            <p>你的角色 <Tag color={roleColors[data.role]}>{roleNames[data.role] || data.role}</Tag> · 项目成员只看到本项目；跨项目依据需双方确认。</p>
          </div>
          <div className="project-collaboration-facts" aria-label="协作状态">
            <div><strong>{canManage ? existingMembers.length : '—'}</strong><span>本项目成员</span></div>
            <div><strong>{canManage ? activeShares : '—'}</strong><span>生效中的共享</span></div>
            <div className={pendingIncoming ? 'needs-attention' : ''}><strong>{canManage ? pendingIncoming : '—'}</strong><span>待我方确认</span></div>
          </div>
        </section>

        {!canManage ? <section className="card project-collaboration-panel project-collaboration-viewer-note">
          <h2>当前为成员视图</h2>
          <p>你可以查看已授权给本项目的公开依据。成员名单与跨项目授权由本项目负责人或管理员维护；加入本项目不会自动获得其它项目权限。</p>
        </section> : null}

        <div className="project-collaboration-grid">

        {canManage ? <section className="card project-collaboration-panel" aria-labelledby="project-members-title">
          <div className="project-collaboration-panel-head"><div><span className="project-collaboration-eyebrow">01 · 人员</span><h2 id="project-members-title">项目成员</h2></div><Tag color="blue">仅本项目</Tag></div>
          <p className="project-collaboration-help">邀请前核对邮箱与角色。账号尚未注册时暂不能登录，但注册后可能立即获得权限；撤权后接口会重新判权。</p>
          <div className="project-collaboration-form">
            <label>Windmill 用户邮箱<Input aria-label="成员邮箱" placeholder="name@example.com" value={email} autoComplete="off"
              onChange={(event) => setEmail(event.target.value)} /></label>
            <label>项目角色<Select aria-label="成员角色" value={role} onChange={setRole}
              options={Object.entries(roleNames).filter(([value]) => data.role === 'owner' || !['owner', 'admin'].includes(value))
                .map(([value, label]) => ({ value, label }))} /></label>
            <Popconfirm title="确认授予该邮箱项目权限？" description="请核对邮箱和角色；此操作会立即改变可见范围。"
              onConfirm={() => void mutate('member_set', { member_email: email.trim(), role })}>
              <Button type="primary" loading={busy} disabled={!email.trim()}>添加或改角色</Button>
            </Popconfirm>
          </div>
          <div className="project-collaboration-subhead"><h3>已加入成员</h3><span>{existingMembers.length} 人</span></div>
          <ul className="project-collaboration-members">
            {existingMembers.map((member) => <li key={member.actor_id}>
              <div className="project-collaboration-member-avatar" aria-hidden="true">{member.actor_id[0]?.toUpperCase() || 'U'}</div>
              <div className="project-collaboration-member-copy"><strong>{member.actor_id}</strong><span>{member.effective_until ? `到期 ${dateLabel(member.effective_until)}` : '长期有效'}</span></div>
              <Tag color={roleColors[member.role]}>{roleNames[member.role] || member.role}</Tag>
              {(data.role === 'owner' || !['owner', 'admin'].includes(member.role)) && !(member.role === 'owner' && ownerCount <= 1) ?
                <Popconfirm title="确认撤销该成员的项目访问？"
                  onConfirm={() => void mutate('member_revoke', { member_email: member.actor_id })}>
                  <Button size="small" disabled={busy}>撤权</Button>
                </Popconfirm> : <span className="project-collaboration-protected">{member.role === 'owner' && ownerCount <= 1 ? '最后一位负责人' : '由负责人管理'}</span>}
            </li>)}
          </ul>
        </section> : null}

        {canManage ? <section className="card project-collaboration-panel" aria-labelledby="project-share-title">
          <div className="project-collaboration-panel-head"><div><span className="project-collaboration-eyebrow">02 · 授权</span><h2 id="project-share-title">项目间共享</h2></div><Tag color="green">双方确认</Tag></div>
          <div className="project-collaboration-boundary"><strong>只共享公开证据</strong>
            <p>仅公开视频标题、公开账号名、合并公开指标及来源项目。审核正文、原始 Provider 响应、媒体、私有授权和费用不会随授权开放。</p></div>
          <div className="project-collaboration-steps" aria-label="共享步骤"><span><b>1</b> 来源项目发邀请</span><span><b>2</b> 接收项目确认</span><span><b>3</b> 到期或撤销即停用</span></div>
          <div className="project-collaboration-form project-collaboration-share-form">
            <label>同组织目标项目<Select aria-label="目标项目" placeholder="选择目标项目" value={target}
              onChange={setTarget} options={data.eligible_targets.map((item) => ({ value: item.id, label: item.name }))} /></label>
            <label>有效期<Select aria-label="授权有效期" value={days} onChange={setDays}
              options={[7, 30, 90, 365].map((value) => ({ value, label: `${value} 天` }))} /></label>
            <Popconfirm title="确认向该项目发出共享邀请？" description="仅限公开证据；对方确认后生效，到期自动失效。"
              onConfirm={() => void mutate('share_offer', { target_project_id: target, expires_days: days })}>
              <Button disabled={!target || busy}>发出共享邀请</Button>
            </Popconfirm>
          </div>
          <div className="project-collaboration-subhead"><h3>发出的邀请与授权</h3><span>{data.outgoing.length} 项</span></div>
          {data.outgoing.length ? <ul className="project-collaboration-grants">{data.outgoing.map((grant) => <li key={grant.id}>
            <div className="project-collaboration-grant-copy"><strong>{grant.other_project_name}</strong><span>接收项目 · 到期 {dateLabel(grant.effective_until)}</span></div>
            <Tag color={grant.status === 'active' ? 'green' : 'gold'}>{grant.status === 'active' ? '生效中' : '待对方确认'}</Tag>
            <Popconfirm title="确认撤销这项共享？" onConfirm={() => void mutate('share_revoke', { grant_id: grant.id })}>
              <Button size="small" disabled={busy}>撤销</Button>
            </Popconfirm>
          </li>)}</ul> : <p className="project-collaboration-empty">尚未向其它项目开放证据。</p>}
          <div className="project-collaboration-subhead"><h3>收到的邀请与授权</h3><span>{data.incoming.length} 项</span></div>
          {data.incoming.length ? <ul className="project-collaboration-grants">{data.incoming.map((grant) => <li key={grant.id}>
            <div className="project-collaboration-grant-copy"><strong>{grant.other_project_name}</strong><span>来源项目 · 到期 {dateLabel(grant.effective_until)}</span></div>
            <Tag color={grant.status === 'active' ? 'green' : 'gold'}>{grant.status === 'active' ? '生效中' : '待我方确认'}</Tag>
            {grant.status === 'offered' ? <div className="project-collaboration-row-actions">
              <Popconfirm title="确认接收公开证据共享？" description="接收后，本项目成员可查看来源项目的公开证据。"
                onConfirm={() => void mutate('share_accept', { grant_id: grant.id })}>
                <Button type="primary" size="small" disabled={busy}>接受</Button>
              </Popconfirm>
              <Button size="small" disabled={busy} onClick={() => void mutate('share_decline', { grant_id: grant.id })}>拒绝</Button>
            </div> : <Popconfirm title="确认停止接收这项共享？" onConfirm={() => void mutate('share_revoke', { grant_id: grant.id })}>
              <Button size="small" disabled={busy}>停止接收</Button>
            </Popconfirm>}
          </li>)}</ul> : <p className="project-collaboration-empty">暂无收到的邀请。其它项目不会自动看到你的资料。</p>}
        </section> : null}
        </div>

        <section className="card project-collaboration-panel project-collaboration-evidence" aria-labelledby="project-evidence-title">
          <div className="project-collaboration-panel-head"><div><span className="project-collaboration-eyebrow">03 · 依据</span><h2 id="project-evidence-title">收到的公开视频依据</h2></div><Tag>{data.shared_videos.length} 条可见</Tag></div>
          <p className="project-collaboration-help">按最近纳入时间展示前 50 条，并非全部共享数据总数。此处只展示可核对的公开事实，不混入来源项目的私有分析。</p>
          {data.shared_videos.length === 0 ? <div className="project-collaboration-evidence-empty"><strong>还没有可查看的共享依据</strong><span>需要来源项目纳入公开视频、发出邀请，并由本项目负责人确认。</span></div> :
            <ul className="project-collaboration-videos">{data.shared_videos.map((video) => <li key={`${video.source_project_id}:${video.video_id}`}>
              <PlatformIcon platform={video.platform} />
              <div className="project-collaboration-video-copy"><strong>{video.title || '未命名视频'}</strong><span>{video.account_name || '未知账号'} · 来自 {video.source_project_name}</span></div>
              <div className="project-collaboration-metrics" aria-label="公开合并指标"><span>播放 <b>{fmt(video.play_count)}</b></span><span>点赞 <b>{fmt(video.like_count)}</b></span><span>评论 <b>{fmt(video.comment_count)}</b></span></div>
            </li>)}</ul>}
        </section>
      </> : null}
    </div>
  </AppShell>
}
