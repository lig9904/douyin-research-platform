import React, { useEffect, useState } from 'react'
import { Alert, Button, Input, Popconfirm, Select, Spin, Tag } from 'antd'
import { backend } from './backend'
import AppShell, { type ResearchView } from './AppShell'
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

export default function ProjectCollaboration({ scope, onNavigate }: {
  scope: ProjectScope
  onNavigate: (view: ResearchView) => void
}) {
  const projectId = scope.mode === 'project' ? scope.projectId : null
  const [data, setData] = useState<Collaboration | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [email, setEmail] = useState('')
  const [role, setRole] = useState('viewer')
  const [target, setTarget] = useState<string | undefined>()
  const [days, setDays] = useState(30)

  const refresh = async () => {
    if (!projectId) return
    setLoading(true)
    setError('')
    try {
      setData(await backend.get_project_collaboration({ project_id: projectId }) as Collaboration)
    } catch (e) {
      setData(null)
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void refresh() }, [projectId])

  const mutate = async (action: string, extra: Record<string, unknown>) => {
    if (!projectId || busy) return
    setBusy(true)
    setError('')
    try {
      await backend.mutate_project_collaboration({ project_id: projectId, action, ...extra })
      await refresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  if (!projectId) return null
  const canManage = data?.role === 'owner' || data?.role === 'admin'
  const existingMembers = data?.members.filter((member) => member.status === 'active' &&
    (!member.effective_until || new Date(member.effective_until).getTime() > Date.now())) || []
  const fmt = (value: number | null | undefined) => value == null ? '—' : Number(value).toLocaleString('zh-CN')

  return <AppShell activeView="collaboration" onNavigate={onNavigate}
    title="项目协作" subtitle="成员逐项目授权；公开视频证据跨项目共享需双方确认"
    actions={<Button onClick={() => void refresh()} disabled={loading || busy}>刷新权限</Button>}>
    <div className="research-section">
      {error ? <Alert type="error" showIcon message="协作操作失败" description={error} /> : null}
      {loading && !data ? <Spin tip="正在核验项目权限" /> : null}
      {data ? <>
        <section className="card">
          <h2>{data.project_name} · 我的角色 <Tag>{roleNames[data.role] || data.role}</Tag></h2>
          <p>同一项目可以有多名成员，一个人也可分别加入多个项目。加入目标项目不会自动获得其它项目的资料。</p>
        </section>

        {canManage ? <section className="card">
          <h2>项目成员</h2>
          <p>请输入拟授权的 Windmill 用户邮箱；若实例账号尚未注册，该邮箱暂不能登录，但日后注册即可能获得此权限。负责人和管理员的调整仅限负责人。撤权后项目接口会立即重新核验。</p>
          <div className="project-collaboration-controls">
            <Input aria-label="成员邮箱" placeholder="已注册成员邮箱" value={email}
              onChange={(event) => setEmail(event.target.value)} style={{ maxWidth: 280 }} />
            <Select aria-label="成员角色" value={role} onChange={setRole} style={{ width: 145 }}
              options={Object.entries(roleNames).filter(([value]) => data.role === 'owner' || !['owner', 'admin'].includes(value))
                .map(([value, label]) => ({ value, label }))} />
            <Popconfirm title="确认授予该邮箱项目权限？" description="请核对邮箱和角色；此操作会立即改变可见范围。"
              onConfirm={() => void mutate('member_set', { member_email: email, role })}>
              <Button type="primary" loading={busy} disabled={!email.trim()}>添加或改角色</Button>
            </Popconfirm>
          </div>
          <ul className="project-collaboration-list">
            {existingMembers.map((member) => <li key={member.actor_id}>
              <span>{member.actor_id} · {roleNames[member.role] || member.role}</span>
              {(data.role === 'owner' || !['owner', 'admin'].includes(member.role)) ?
                <Popconfirm title="确认撤销该成员的项目访问？"
                  onConfirm={() => void mutate('member_revoke', { member_email: member.actor_id })}>
                  <Button danger size="small" disabled={busy}>撤权</Button>
                </Popconfirm> : null}
            </li>)}
          </ul>
        </section> : null}

        {canManage ? <section className="card">
          <h2>项目间共享</h2>
          <p>默认不共享。当前仅支持“公开视频标题、公开账号名、合并公开指标”及来源项目标识；审核正文、原始 Provider 响应、媒体、私有授权和费用不会随授权开放。接收项目负责人/管理员确认后才生效。</p>
          <div className="project-collaboration-controls">
            <Select aria-label="目标项目" placeholder="选择同组织目标项目" value={target}
              onChange={setTarget} style={{ minWidth: 220 }}
              options={data.eligible_targets.map((item) => ({ value: item.id, label: item.name }))} />
            <Select aria-label="授权有效期" value={days} onChange={setDays} style={{ width: 125 }}
              options={[7, 30, 90, 365].map((value) => ({ value, label: `${value} 天` }))} />
            <Popconfirm title="确认向该项目发出共享邀请？" description="仅限公开证据；对方确认后生效，到期自动失效。"
              onConfirm={() => void mutate('share_offer', { target_project_id: target, expires_days: days })}>
              <Button disabled={!target || busy}>发出共享邀请</Button>
            </Popconfirm>
          </div>
          <h3>发出的授权</h3>
          <ul className="project-collaboration-list">{data.outgoing.map((grant) => <li key={grant.id}>
            <span>{grant.other_project_name} · {grant.status === 'active' ? '生效中' : '待对方确认'} · 到期 {new Date(grant.effective_until).toLocaleDateString('zh-CN')}</span>
            <Popconfirm title="确认撤销这项共享？" onConfirm={() => void mutate('share_revoke', { grant_id: grant.id })}>
              <Button danger size="small" disabled={busy}>撤销</Button>
            </Popconfirm>
          </li>)}</ul>
          <h3>收到的授权</h3>
          <ul className="project-collaboration-list">{data.incoming.map((grant) => <li key={grant.id}>
            <span>{grant.other_project_name} · {grant.status === 'active' ? '已接受' : '待确认'} · 到期 {new Date(grant.effective_until).toLocaleDateString('zh-CN')}</span>
            {grant.status === 'offered' ? <>
              <Popconfirm title="确认接收公开证据共享？" onConfirm={() => void mutate('share_accept', { grant_id: grant.id })}>
                <Button size="small" disabled={busy}>接受</Button>
              </Popconfirm>
              <Button size="small" disabled={busy} onClick={() => void mutate('share_decline', { grant_id: grant.id })}>拒绝</Button>
            </> : <Popconfirm title="确认停止接收这项共享？" onConfirm={() => void mutate('share_revoke', { grant_id: grant.id })}>
              <Button danger size="small" disabled={busy}>停止接收</Button>
            </Popconfirm>}
          </li>)}</ul>
        </section> : null}

        <section className="card">
          <h2>收到的公开视频依据 <Tag>{data.shared_videos.length}</Tag></h2>
          <p>按最近纳入时间展示前 50 条；这不是全部共享数据的总数。</p>
          {data.shared_videos.length === 0 ? <p>暂无已接受、未到期的共享证据。</p> :
            <ul className="project-collaboration-list">{data.shared_videos.map((video) => <li key={`${video.source_project_id}:${video.video_id}`}>
              <span><strong>{video.title || '未命名视频'}</strong> · {video.account_name || '未知账号'} · 来自 {video.source_project_name}<br />
                公开指标：播放 {fmt(video.play_count)} · 点赞 {fmt(video.like_count)} · 评论 {fmt(video.comment_count)}</span>
            </li>)}</ul>}
        </section>
      </> : null}
    </div>
  </AppShell>
}
