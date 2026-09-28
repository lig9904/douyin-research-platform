import React, { useEffect, useRef, useState } from 'react'
import { Alert, Button, Input, Select, Spin, Tag } from 'antd'
import { backend } from './backend'
import AppShell, { type ResearchView } from './AppShell'
import type { ProjectScope } from './src/projectScope'
import './project-concepts.css'

type Status = 'draft' | 'ready_for_internal_test' | 'withdrawn'
type ConceptFields = {
  title: string
  premise: string
  character_choice: string
  episode_payoff: string
  evidence_note: string
  test_question: string
  production_constraints: string
  status: Status
}
type Concept = ConceptFields & {
  id: string
  version_no: number
  created_by: string
  created_at: string
  recorded_by: string
  recorded_at: string
}
type ConceptList = {
  project_name: string
  role: string
  concepts: Concept[]
  boundary: string
  truncated: boolean
}
type History = { concept_id: string; revisions: (ConceptFields & { version_no: number; recorded_by: string; recorded_at: string })[]; has_more: boolean }

const fields: (keyof Omit<ConceptFields, 'status'>)[] = [
  'title', 'premise', 'character_choice', 'episode_payoff',
  'evidence_note', 'test_question', 'production_constraints',
]
const labels: Record<(typeof fields)[number], string> = {
  title: '选题名称',
  premise: '一句话事件与人物目标',
  character_choice: '角色自己的选择与代价',
  episode_payoff: '本条可见回报',
  evidence_note: '研究依据与尚未核实处',
  test_question: '低保真试片要验证的问题',
  production_constraints: '设定、素材权利与制作边界',
}
const limits: Record<(typeof fields)[number], number> = {
  title: 160, premise: 1200, character_choice: 1200,
  episode_payoff: 1200, evidence_note: 1200, test_question: 800,
  production_constraints: 1200,
}
const empty = (): ConceptFields => ({
  title: '', premise: '', character_choice: '', episode_payoff: '',
  evidence_note: '', test_question: '', production_constraints: '', status: 'draft',
})
const statusLabel: Record<Status, string> = {
  draft: '草稿', ready_for_internal_test: '待内部低保真试片', withdrawn: '已撤回',
}
const canWrite = new Set(['owner', 'admin', 'researcher'])
const readableTime = (value: string) => {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '时间待核' : date.toLocaleString('zh-CN')
}

export default function ProjectConcepts({ scope, onNavigate }: { scope: ProjectScope; onNavigate: (view: ResearchView) => void }) {
  const projectId = scope.mode === 'project' ? scope.projectId : null
  const [data, setData] = useState<ConceptList | null>(null)
  const [loadedProjectId, setLoadedProjectId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [form, setForm] = useState<ConceptFields>(empty)
  const [editing, setEditing] = useState<Concept | null>(null)
  const [history, setHistory] = useState<History | null>(null)
  const version = useRef(0)
  const activeProject = useRef(projectId)
  activeProject.current = projectId
  const attempt = useRef<{ fingerprint: string; key: string } | null>(null)

  const refresh = async () => {
    if (!projectId) return false
    const request = ++version.current
    setLoading(true); setError('')
    try {
      const result = await backend.project_creative_concepts({ project_id: projectId, action: 'list' }) as ConceptList
      if (request === version.current) { setData(result); setLoadedProjectId(projectId); return true }
    } catch (e) {
      if (request === version.current) { setData(null); setLoadedProjectId(null); setError(e instanceof Error ? e.message : String(e)) }
    } finally { if (request === version.current) setLoading(false) }
    return false
  }
  useEffect(() => {
    setData(null); setLoadedProjectId(null); setHistory(null)
    setEditing(null); setForm(empty()); attempt.current = null
    void refresh()
    return () => { version.current += 1 }
  }, [projectId])

  const currentData = loadedProjectId === projectId ? data : null
  const writable = !!currentData && canWrite.has(currentData.role)
  const valid = fields.every(name => form[name].trim().length > 0 && form[name].trim().length <= limits[name])
    && (editing ? true : form.status !== 'withdrawn')

  const reset = () => { setEditing(null); setForm(empty()); attempt.current = null }
  const edit = (concept: Concept) => {
    setEditing(concept)
    setForm(Object.fromEntries([...fields.map(name => [name, concept[name]]), ['status', concept.status]]) as ConceptFields)
    setNotice(''); setError(''); attempt.current = null
  }
  const save = async () => {
    if (!projectId || !writable || busy || !valid) return
    const action = editing ? 'revise' : 'create'
    const payload = editing
      ? { ...form, concept_id: editing.id, expected_version: editing.version_no }
      : { ...form }
    const fingerprint = JSON.stringify({ projectId, action, payload })
    if (attempt.current?.fingerprint !== fingerprint) attempt.current = { fingerprint, key: crypto.randomUUID() }
    const key = attempt.current.key
    setBusy(true); setError(''); setNotice('')
    try {
      await backend.project_creative_concepts({ project_id: projectId, action, payload, idempotency_key: key })
      attempt.current = null
      if (activeProject.current !== projectId) return
      const fresh = await refresh()
      if (activeProject.current !== projectId) return
      setNotice(fresh ? '选题新版本已保存并回读。仍属内部提案，不是行动卡或发布批准。' : '已提交，最新状态需刷新后核对。')
      if (fresh) reset()
    } catch (e) {
      if (activeProject.current === projectId) setError(`${e instanceof Error ? e.message : String(e)}；如结果不确定，保持表单重试会复用同一提交键。`)
    } finally { setBusy(false) }
  }
  const showHistory = async (conceptId: string) => {
    if (!projectId) return
    setError('')
    try {
      const result = await backend.project_creative_concepts({ project_id: projectId, action: 'history', payload: { concept_id: conceptId } }) as History
      if (activeProject.current === projectId) setHistory(result)
    } catch (e) { if (activeProject.current === projectId) setError(e instanceof Error ? e.message : String(e)) }
  }

  if (!projectId) return null
  return <AppShell activeView="concepts" onNavigate={onNavigate} title="选题草稿" subtitle="把原创方向变成可试片、可修订的项目私有提案"
    actions={<Button onClick={() => void refresh()} disabled={loading || busy}>刷新</Button>} mainClassName="project-concepts-page">
    <div className="project-concepts-content">
      {error ? <Alert type="error" showIcon message="选题状态未能确认" description={error} /> : null}
      {notice ? <Alert type="success" showIcon message={notice} closable onClose={() => setNotice('')} /> : null}
      {loading && !currentData ? <div className="project-concepts-loading"><Spin tip="正在核验项目范围" /></div> : null}
      {currentData ? <>
        <section className="card project-concepts-intro">
          <div><small>内部策划 · {currentData.project_name}</small><h2>先形成可反驳的原创试片，再决定是否采用</h2><p>{currentData.boundary}</p></div>
          <div className="project-concepts-count"><strong>{currentData.concepts.filter(item => item.status !== 'withdrawn').length}</strong><span>当前提案</span></div>
        </section>
        {currentData.truncated ? <Alert type="warning" showIcon message="只展示最近 100 条选题；更早记录仍在项目数据库中。" /> : null}
        {writable ? <section className="card project-concepts-form" aria-label="原创选题表单">
          <div className="project-concepts-heading"><div><small>{editing ? `修订 v${editing.version_no + 1}` : '新建 v1'}</small><h2>{editing ? `修订：${editing.title}` : '记录原创选题'}</h2></div>{editing ? <Button onClick={reset} disabled={busy}>取消修订</Button> : null}</div>
          <p>不要求先有完整公开视频 Case；这里不能生成“已采用行动”、正式素材授权或对外发布记录。每次修改追加版本，不覆盖旧稿。</p>
          <div className="project-concepts-fields">
            {fields.map(name => <label key={name}>{labels[name]}
              {name === 'title'
                ? <Input aria-label={labels[name]} maxLength={limits[name]} value={form[name]} onChange={e => setForm({ ...form, [name]: e.target.value })} />
                : <Input.TextArea aria-label={labels[name]} maxLength={limits[name]} autoSize={{ minRows: 2, maxRows: 5 }} value={form[name]} onChange={e => setForm({ ...form, [name]: e.target.value })} />}
            </label>)}
            <label>内部状态<Select aria-label="内部状态" value={form.status} options={Object.entries(statusLabel).filter(([value]) => editing || value !== 'withdrawn').map(([value, label]) => ({ value, label }))} onChange={value => setForm({ ...form, status: value as Status })} /></label>
          </div>
          {form.status === 'withdrawn' ? <Alert type="warning" showIcon message="撤回将追加终止版本，之后不能继续修订；原历史保留。" /> : null}
          <Button type="primary" onClick={() => void save()} loading={busy} disabled={!valid || busy}>{editing ? '保存新版本' : '保存选题草稿'}</Button>
        </section> : <section className="card project-concepts-readonly">当前为只读成员；可查看本项目选题及其版本，不能修改。</section>}
        <section className="project-concepts-list" aria-label="项目选题列表">
          <div className="project-concepts-heading"><div><small>项目私有</small><h2>现有选题</h2></div></div>
          {!currentData.concepts.length ? <div className="card project-concepts-empty">暂无原创选题草稿。研究片的机器观察仍在视频库；不会被自动写成项目决策。</div> : null}
          {currentData.concepts.map(item => <article className="card project-concept-card" key={item.id}>
            <div className="project-concepts-heading"><div><small>v{item.version_no} · {readableTime(item.recorded_at)} · {item.recorded_by}</small><h3>{item.title}</h3></div><Tag color={item.status === 'withdrawn' ? 'default' : item.status === 'ready_for_internal_test' ? 'blue' : 'gold'}>{statusLabel[item.status]}</Tag></div>
            <dl>
              <div><dt>事件与目标</dt><dd>{item.premise}</dd></div>
              <div><dt>九九的选择与代价</dt><dd>{item.character_choice}</dd></div>
              <div><dt>本条回报</dt><dd>{item.episode_payoff}</dd></div>
              <div><dt>依据与缺口</dt><dd>{item.evidence_note}</dd></div>
              <div><dt>试片问题</dt><dd>{item.test_question}</dd></div>
              <div><dt>制作边界</dt><dd>{item.production_constraints}</dd></div>
            </dl>
            <div className="project-concepts-actions">
              {writable && item.status !== 'withdrawn' ? <Button onClick={() => edit(item)}>追加修订</Button> : null}
              <Button onClick={() => void showHistory(item.id)}>查看版本历史</Button>
            </div>
            {history?.concept_id === item.id ? <div className="project-concepts-history"><strong>历史版本</strong>{history.revisions.map(revision => <div key={revision.version_no}>v{revision.version_no} · {statusLabel[revision.status]} · {readableTime(revision.recorded_at)} · {revision.recorded_by} · {revision.title}</div>)}{history.has_more ? <small>仅显示最近 100 版；更早版本仍保留在数据库。</small> : null}</div> : null}
          </article>)}
        </section>
      </> : null}
    </div>
  </AppShell>
}
