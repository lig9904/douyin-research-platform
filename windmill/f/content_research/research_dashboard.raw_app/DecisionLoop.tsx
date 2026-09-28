import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Button, Input, Select, Spin, Tag } from 'antd'
import { backend } from './backend'
import AppShell, { type ResearchView } from './AppShell'
import type { ProjectScope } from './src/projectScope'
import './decision-loop.css'

type Video = { id: string; title?: string | null; account_name?: string | null; platform_video_id: string }
type Card = {
  id: string; source_video_id: string; subject_id?: string | null; subject_name?: string | null; source_video_title?: string | null; source_account_name?: string | null
  hypothesis: string; reference_point: string; adaptation_difference: string; owner_actor: string
  decision: 'adopt' | 'observe' | 'exclude'; status: string; source_reference_withdrawn?: boolean; review_conclusion?: string | null; review_evidence?: string | null; next_action?: string | null; updated_at: string
}
type Event = { decision_card_id: string; action: string; from_status?: string | null; to_status?: string | null; actor_id: string; created_at: string }
type Publication = {
  id: string; decision_card_id: string; publication_date: string; title: string; content_reference: string; status: string
  daily_observations: { id: string; metric_date: string; version: number; impressions: number | null; engagements: number | null; likes: number | null; comments: number | null; shares: number | null; follows: number | null; conversions: number | null; source: string; source_reference: string; source_reported_at: string; source_version_or_digest: string; measurement_scope: string }[]
}
type Loop = { project_name: string; role: string; cards: Card[]; events: Event[]; eligible_videos: Video[]; subjects: { id: string; name: string; subject_type: string }[]; publications: Publication[]; boundaries: { card_source: string; outcomes: string } }
type Review = { card_id: string; observation_id: string; conclusion: string; evidence: string; next_action: string }

const decisions = [
  { value: 'adopt', label: '采用' }, { value: 'observe', label: '观察' }, { value: 'exclude', label: '排除' },
]
const statusNames: Record<string, string> = { draft: '草稿', active: '执行中', observing: '观察中', adopted: '已采用', excluded: '已排除', reviewed: '已复盘', archived: '已归档', published: '已发布' }
const statusColors: Record<string, string> = { draft: 'default', active: 'blue', observing: 'gold', adopted: 'green', excluded: 'red', reviewed: 'cyan', archived: 'default', published: 'green' }
const writable = new Set(['owner', 'admin', 'researcher'])
const statusTransitions: Record<string, string[]> = { active: ['adopted', 'archived'], observing: ['archived'], adopted: ['archived'], excluded: ['archived'] }
const emptyMetrics = () => ({ impressions: null as number | null, engagements: null as number | null, likes: null as number | null, comments: null as number | null, shares: null as number | null, follows: null as number | null, conversions: null as number | null })

function today() { return new Date().toISOString().slice(0, 10) }
function dateTime(value: string) { const parsed = new Date(value); return Number.isNaN(parsed.getTime()) ? '时间待核对' : parsed.toLocaleString('zh-CN') }

export default function DecisionLoop({ scope, onNavigate }: { scope: ProjectScope; onNavigate: (view: ResearchView) => void }) {
  const projectId = scope.mode === 'project' ? scope.projectId : null
  const [data, setData] = useState<Loop | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [cardForm, setCardForm] = useState({ source_video_id: '', subject_id: null as string | null, hypothesis: '', reference_point: '', adaptation_difference: '', owner_actor: '', decision: 'observe' })
  const [publication, setPublication] = useState({ decision_card_id: '', publication_date: today(), title: '', content_reference: '' })
  const [metric, setMetric] = useState({ publication_id: '', metric_date: today(), source: 'manual', source_reference: '', source_reported_at: new Date().toISOString(), source_version_or_digest: '', measurement_scope: '', ...emptyMetrics() })
  const [review, setReview] = useState<Review>({ card_id: '', observation_id: '', conclusion: '', evidence: '', next_action: '' })
  const version = useRef(0)

  const refresh = async () => {
    if (!projectId) return false
    const request = ++version.current
    setLoading(true); setError('')
    try {
      const result = await backend.get_project_decision_loop({ project_id: projectId }) as Loop
      if (request === version.current) { setData(result); return true }
    } catch (e) {
      if (request === version.current) { setData(null); setError(e instanceof Error ? e.message : String(e)) }
    } finally { if (request === version.current) setLoading(false) }
    return false
  }
  useEffect(() => { void refresh(); return () => { version.current += 1 } }, [projectId])
  const canWrite = data ? writable.has(data.role) : false
  const latestEvent = useMemo(() => new Map(data?.events.map(event => [event.decision_card_id, event]) || []), [data])

  const mutate = async (action: string, payload: Record<string, unknown>, done: () => void) => {
    if (!projectId || busy || !canWrite) return
    setBusy(true); setError(''); setNotice('')
    try {
      await backend.mutate_project_decision_loop({ project_id: projectId, action, payload, idempotency_key: crypto.randomUUID() })
      const fresh = await refresh()
      setNotice(fresh ? '已保存，并已重新核验项目数据。' : '已提交，但最新状态需刷新后核对。')
      done()
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }
  if (!projectId) return null

  return <AppShell activeView="decisions" onNavigate={onNavigate} title="行动复盘" subtitle="把公开参考转为可验证的项目行动与非个人汇总结果"
    actions={<Button onClick={() => void refresh()} disabled={loading || busy}>刷新</Button>} mainClassName="decision-loop-page">
    <div className="decision-loop-content">
      {error ? <Alert type="error" showIcon message="行动复盘数据未能核验" description={error} /> : null}
      {notice ? <Alert type="success" showIcon closable onClose={() => setNotice('')} message={notice} /> : null}
      {loading && !data ? <div className="decision-loop-loading"><Spin tip="正在核验项目范围" /></div> : null}
      {data ? <>
        <section className="card decision-loop-intro">
          <div><span>项目决策闭环</span><h2>{data.project_name}</h2><p>参考不是照搬：写清假设、可借鉴点、我方差异和负责人，再用发布后的非个人汇总结果复盘。</p></div>
          <div className="decision-loop-counts"><div><b>{data.cards.length}</b><small>行动卡</small></div><div><b>{data.publications.length}</b><small>我方内容</small></div></div>
        </section>
        <section className="card decision-loop-boundary"><strong>边界</strong><span>{data.boundaries.card_source}</span><span>{data.boundaries.outcomes}</span></section>
        {!canWrite ? <section className="card decision-loop-readonly"><h2>当前为只读成员</h2><p>你可以查看本项目行动与复盘结果；创建、修改和状态推进仅对负责人、管理员和研究员开放。</p></section> : null}
        {canWrite ? <section className="card decision-loop-form-card">
          <div className="decision-loop-heading"><div><span>01 · 参考转行动</span><h2>新建行动卡</h2></div><Tag color="blue">仅本项目已接受视频</Tag></div>
          {!data.eligible_videos.length ? <p className="decision-loop-empty">没有可用参考视频。请先在“视频库”将公开视频明确纳入本项目；跨项目共享视频仅供阅读，不能在此建卡。</p> : <div className="decision-loop-form">
            <label>参考视频<Select aria-label="参考视频" value={cardForm.source_video_id || undefined} placeholder="选择已接受的公开视频" options={data.eligible_videos.map(video => ({ value: video.id, label: `${video.title || '未命名视频'} · ${video.account_name || '未知账号'}` }))} onChange={value => setCardForm({ ...cardForm, source_video_id: value })} /></label>
            <label>决策<Select aria-label="决策" value={cardForm.decision} options={decisions} onChange={value => setCardForm({ ...cardForm, decision: value })} /></label>
            <label>关联主体（可选）<Select aria-label="关联主体" allowClear value={cardForm.subject_id || undefined} placeholder="不关联主体" options={data.subjects.map(subject => ({ value: subject.id, label: `${subject.name} · ${subject.subject_type}` }))} onChange={value => setCardForm({ ...cardForm, subject_id: value || null })} /></label>
            <label>负责人<Input aria-label="负责人" value={cardForm.owner_actor} placeholder="负责人邮箱" onChange={e => setCardForm({ ...cardForm, owner_actor: e.target.value })} /></label>
            <label>假设<Input.TextArea aria-label="假设" autoSize={{ minRows: 2, maxRows: 5 }} value={cardForm.hypothesis} onChange={e => setCardForm({ ...cardForm, hypothesis: e.target.value })} /></label>
            <label>可借鉴点<Input.TextArea aria-label="可借鉴点" autoSize={{ minRows: 2, maxRows: 5 }} value={cardForm.reference_point} onChange={e => setCardForm({ ...cardForm, reference_point: e.target.value })} /></label>
            <label>我方差异与边界<Input.TextArea aria-label="我方差异与边界" autoSize={{ minRows: 2, maxRows: 5 }} value={cardForm.adaptation_difference} onChange={e => setCardForm({ ...cardForm, adaptation_difference: e.target.value })} /></label>
            <Button type="primary" loading={busy} disabled={!cardForm.source_video_id || !cardForm.hypothesis.trim() || !cardForm.reference_point.trim() || !cardForm.adaptation_difference.trim() || !cardForm.owner_actor.trim()} onClick={() => void mutate('create_card', cardForm, () => setCardForm({ source_video_id: '', subject_id: null, hypothesis: '', reference_point: '', adaptation_difference: '', owner_actor: '', decision: 'observe' }))}>保存行动卡</Button>
          </div>}
        </section> : null}
        <section className="decision-loop-card-grid" aria-label="行动卡列表">
          {data.cards.map(card => <article className="card decision-card" key={card.id}>
            <div className="decision-card-head"><div><small>{card.source_account_name || '未知账号'} · {card.source_video_title || '未命名参考视频'}</small><h2>{card.hypothesis}</h2></div><Tag color={statusColors[card.status]}>{statusNames[card.status] || card.status}</Tag></div>
            <dl><div><dt>可借鉴点</dt><dd>{card.reference_point}</dd></div><div><dt>我方差异</dt><dd>{card.adaptation_difference}</dd></div></dl>
            {card.subject_name ? <p className="decision-card-subject">关联主体：{card.subject_name}</p> : null}
            {card.source_reference_withdrawn ? <p className="decision-card-withdrawn">参考已从当前项目接受列表撤回或公开源已不可用；保留此卡仅用于历史复盘，不会作为新建卡来源。</p> : null}
            {card.status === 'reviewed' ? <div className="decision-card-review"><strong>复盘结论</strong><p>{card.review_conclusion}</p><small>依据：{card.review_evidence}</small><small>下一动作：{card.next_action}</small></div> : null}
            <div className="decision-card-foot"><span>负责人：{card.owner_actor}</span><Tag color={card.decision === 'adopt' ? 'green' : card.decision === 'exclude' ? 'red' : 'gold'}>{decisions.find(item => item.value === card.decision)?.label}</Tag>{canWrite && statusTransitions[card.status]?.length ? <Select size="small" placeholder="推进状态" options={statusTransitions[card.status].map(value => ({ value, label: statusNames[value] }))} onChange={status => void mutate('set_card_status', { card_id: card.id, status }, () => undefined)} /> : null}</div>
            {latestEvent.get(card.id) ? <p className="decision-card-audit">最近审计：{latestEvent.get(card.id)?.actor_id} · {latestEvent.get(card.id)?.action === 'status_changed' ? `${statusNames[latestEvent.get(card.id)?.from_status || '']} → ${statusNames[latestEvent.get(card.id)?.to_status || '']}` : latestEvent.get(card.id)?.action} · {dateTime(latestEvent.get(card.id)?.created_at || '')}</p> : null}
          </article>)}
          {!data.cards.length ? <div className="card decision-loop-empty">尚未建立行动卡。先在视频库接受公开视频，再把可借鉴之处和我方差异写清。</div> : null}
        </section>
        {canWrite ? <section className="card decision-loop-form-card">
          <div className="decision-loop-heading"><div><span>02 · 我方实践</span><h2>登记发布与按日结果</h2></div><Tag color="green">非个人汇总</Tag></div>
          {!data.cards.length ? <p className="decision-loop-empty">需先建立行动卡，才可登记与该行动关联的我方内容。</p> : <div className="decision-loop-form decision-loop-publication-form">
            <label>关联行动<Select aria-label="关联行动" value={publication.decision_card_id || undefined} placeholder="选择行动卡" options={data.cards.map(card => ({ value: card.id, label: card.hypothesis }))} onChange={value => setPublication({ ...publication, decision_card_id: value })} /></label>
            <label>发布日期<Input aria-label="发布日期" type="date" value={publication.publication_date} onChange={e => setPublication({ ...publication, publication_date: e.target.value })} /></label>
            <label>内容名称<Input aria-label="内容名称" value={publication.title} onChange={e => setPublication({ ...publication, title: e.target.value })} /></label>
            <label>内容引用<Input aria-label="内容引用" placeholder="内部编号或公开链接" value={publication.content_reference} onChange={e => setPublication({ ...publication, content_reference: e.target.value })} /></label>
            <Button loading={busy} disabled={!publication.decision_card_id || !publication.title.trim() || !publication.content_reference.trim()} onClick={() => void mutate('create_publication', publication, () => setPublication({ decision_card_id: '', publication_date: today(), title: '', content_reference: '' }))}>登记已发布内容</Button>
          </div>}
          {data.publications.length ? <div className="decision-loop-metric-form"><label>内容<Select aria-label="登记指标的内容" value={metric.publication_id || undefined} placeholder="选择我方内容" options={data.publications.map(item => ({ value: item.id, label: item.title }))} onChange={value => setMetric({ ...metric, publication_id: value })} /></label><label>统计日<Input aria-label="统计日" type="date" value={metric.metric_date} onChange={e => setMetric({ ...metric, metric_date: e.target.value })} /></label><label>来源<Select aria-label="指标来源" value={metric.source} options={[{ value: 'manual', label: '人工汇总（待人工核对）' }, { value: 'imported_aggregate', label: '导入汇总（以来源凭据为准）' }]} onChange={value => setMetric({ ...metric, source: value })} /></label><label>来源记录号/报表引用<Input aria-label="来源记录号或报表引用" placeholder="人工：内部记录号；导入：账单/报表引用" value={metric.source_reference} onChange={e => setMetric({ ...metric, source_reference: e.target.value })} /></label><label>来源报告时间（含时区）<Input aria-label="来源报告时间" placeholder="2026-09-24T10:00:00+08:00" value={metric.source_reported_at} onChange={e => setMetric({ ...metric, source_reported_at: e.target.value })} /></label><label>来源版本或摘要<Input aria-label="来源版本或摘要" placeholder="版本号或文件摘要" value={metric.source_version_or_digest} onChange={e => setMetric({ ...metric, source_version_or_digest: e.target.value })} /></label><label>观察口径<Input aria-label="观察口径" placeholder="如发布后24小时累计" value={metric.measurement_scope} onChange={e => setMetric({ ...metric, measurement_scope: e.target.value })} /></label>{Object.keys(emptyMetrics()).map(name => <label key={name}>{({ impressions: '曝光', engagements: '互动', likes: '点赞', comments: '评论数', shares: '分享', follows: '关注', conversions: '转化' } as Record<string, string>)[name]}<Input aria-label={name} type="number" min="0" placeholder="未知留空" value={metric[name as keyof typeof metric] ?? ''} onChange={e => setMetric({ ...metric, [name]: e.target.value === '' ? null : Number(e.target.value) })} /></label>)}<Button type="primary" loading={busy} disabled={!metric.publication_id || !metric.source_reference.trim() || !metric.source_reported_at.trim() || !metric.source_version_or_digest.trim() || !metric.measurement_scope.trim() || Object.keys(emptyMetrics()).every(name => metric[name as keyof typeof metric] == null)} onClick={() => void mutate('record_daily_metric', { publication_id: metric.publication_id, metric_date: metric.metric_date, source: metric.source, source_reference: metric.source_reference, source_reported_at: metric.source_reported_at, source_version_or_digest: metric.source_version_or_digest, measurement_scope: metric.measurement_scope, metrics: Object.fromEntries(Object.keys(emptyMetrics()).map(name => [name, metric[name as keyof typeof metric]])) }, () => setMetric({ publication_id: '', metric_date: today(), source: 'manual', source_reference: '', source_reported_at: new Date().toISOString(), source_version_or_digest: '', measurement_scope: '', ...emptyMetrics() }))}>追加日汇总版本</Button></div> : null}
        </section> : null}
        {canWrite ? <section className="card decision-loop-form-card"><div className="decision-loop-heading"><div><span>03 · 复盘结论</span><h2>用锁定的实际汇总完成复盘</h2></div><Tag color="cyan">结果后可填</Tag></div><p className="decision-loop-review-help">选择一个已记录的观察版本；保存复盘后会绑定其 ID 和版本，后续指标修订不会改写本次复盘依据。</p><div className="decision-loop-form"><label>行动卡<Select aria-label="待复盘行动卡" value={review.card_id || undefined} placeholder="选择已产出结果的行动卡" options={data.cards.filter(card => card.status !== 'reviewed').map(card => ({ value: card.id, label: card.hypothesis }))} onChange={value => setReview({ ...review, card_id: value, observation_id: '' })} /></label><label>观察版本<Select aria-label="复盘观察版本" value={review.observation_id || undefined} placeholder="选择已记录汇总" options={data.publications.filter(item => item.decision_card_id === review.card_id).flatMap(item => item.daily_observations.map(observation => ({ value: observation.id, label: `${item.title} · ${observation.metric_date} v${observation.version}` })))} onChange={value => setReview({ ...review, observation_id: value })} /></label><label>复盘结论<Input.TextArea aria-label="复盘结论" autoSize={{ minRows: 2, maxRows: 5 }} value={review.conclusion} onChange={e => setReview({ ...review, conclusion: e.target.value })} /></label><label>结果依据（仅汇总）<Input.TextArea aria-label="结果依据" autoSize={{ minRows: 2, maxRows: 5 }} value={review.evidence} onChange={e => setReview({ ...review, evidence: e.target.value })} /></label><label>下一动作<Input.TextArea aria-label="下一动作" autoSize={{ minRows: 2, maxRows: 5 }} value={review.next_action} onChange={e => setReview({ ...review, next_action: e.target.value })} /></label><Button type="primary" loading={busy} disabled={!review.observation_id || !review.card_id || !review.conclusion.trim() || !review.evidence.trim() || !review.next_action.trim()} onClick={() => void mutate('record_review', review, () => setReview({ card_id: '', observation_id: '', conclusion: '', evidence: '', next_action: '' }))}>保存复盘结论</Button></div></section> : null}
        <section className="card decision-loop-results"><div className="decision-loop-heading"><div><span>04 · 结果看板</span><h2>我方内容与日汇总</h2></div></div>{data.publications.length ? <div className="decision-publication-list">{data.publications.map(item => <article key={item.id}><div><strong>{item.title}</strong><span>{item.publication_date} · {item.content_reference} · <Tag color={statusColors[item.status]}>{statusNames[item.status] || item.status}</Tag></span></div><div className="decision-metric-summary">{item.daily_observations[0] ? <>最近 {item.daily_observations[0].metric_date} v{item.daily_observations[0].version}：曝光 {item.daily_observations[0].impressions == null ? '未知' : Number(item.daily_observations[0].impressions).toLocaleString('zh-CN')} · 互动 {item.daily_observations[0].engagements == null ? '未知' : Number(item.daily_observations[0].engagements).toLocaleString('zh-CN')} · 转化 {item.daily_observations[0].conversions == null ? '未知' : Number(item.daily_observations[0].conversions).toLocaleString('zh-CN')}</> : '暂未登记日汇总'}</div></article>)}</div> : <p className="decision-loop-empty">暂无我方发布记录。行动卡推进后，可在这里登记非个人化的每日汇总结果。</p>}</section>
      </> : null}
    </div>
  </AppShell>
}
