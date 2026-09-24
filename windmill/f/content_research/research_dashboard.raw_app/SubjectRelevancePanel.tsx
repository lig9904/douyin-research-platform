import React, { useEffect, useMemo, useState } from 'react'
import { Alert, Button, Card, Form, Input, Modal, Select, Space, Table, Tag, message } from 'antd'
import { backend } from './backend'

type Term = { id: string; term: string; term_type: 'alias' | 'geographic_context' | 'exclusion' }
export type ProjectSubject = {
  id: string
  name: string
  subject_type: string
  terms: Term[]
  relevant_count: number
  pending_count: number
  irrelevant_count: number
}
type Review = {
  subject_id: string
  video_id: string
  decision: 'pending' | 'relevant' | 'irrelevant'
  decision_source: 'rule' | 'manual'
  title: string
  account_name?: string | null
  subject_name: string
  run_id?: string | null
  match_detail?: { aliases?: string[]; geographic_contexts?: string[]; exclusions?: string[]; invalidated_by?: string }
  reviewed_by?: string | null
  reviewed_at?: string | null
}
type ProjectScore = {
  subject_id: string
  video_id: string
  score: number
  confidence?: 'high' | 'medium' | 'low' | null
  title: string
  account_name?: string | null
  rule_version: string
  created_at: string
  source_run_id: string
}
type Data = { subjects: ProjectSubject[]; review_queue: Review[]; top_scores: ProjectScore[] }
type Values = { name: string; subject_type: string; aliases: string; geographic_contexts: string; exclusions: string }

const typeNames: Record<string, string> = {
  destination: '目的地', ip: 'IP', character: '角色', product: '产品', activity: '活动',
  brand: '品牌', account: '账号', topic: '议题', other: '其他',
}
const decisionColor = { relevant: 'green', pending: 'gold', irrelevant: 'default' } as const
const decisionName = { relevant: '相关', pending: '待判定', irrelevant: '排除' }
const split = (value?: string) => Array.from(new Set((value || '').split(/[\n,，]/).map((item) => item.trim()).filter(Boolean)))
const join = (terms: Term[], kind: Term['term_type']) => terms.filter((term) => term.term_type === kind).map((term) => term.term).join('，')

export default function SubjectRelevancePanel({ projectId, selectedSubjectId, onSelectSubject, onSubjectsChange, onOpenVideoLibrary }: {
  projectId: string
  selectedSubjectId?: string
  onSelectSubject: (id: string) => void
  onSubjectsChange?: (subjects: ProjectSubject[]) => void
  onOpenVideoLibrary?: () => void
}) {
  const [data, setData] = useState<Data>({ subjects: [], review_queue: [], top_scores: [] })
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [modal, setModal] = useState<'create' | 'terms' | null>(null)
  const [editing, setEditing] = useState<ProjectSubject | null>(null)
  const [form] = Form.useForm<Values>()
  const [toast, contextHolder] = message.useMessage()

  const load = async () => {
    setError('')
    try {
      const next = await backend.get_project_subjects({ project_id: projectId }) as Data
      setData(next)
      onSubjectsChange?.(next.subjects)
      if (!selectedSubjectId && next.subjects.length) onSelectSubject(next.subjects[0].id)
    } catch (e) {
      setError(e instanceof Error ? e.message : '主体资料加载失败')
    }
  }
  useEffect(() => { void load() }, [projectId])
  const selected = useMemo(() => data.subjects.find((subject) => subject.id === selectedSubjectId) || null, [data.subjects, selectedSubjectId])

  const openCreate = () => {
    setEditing(null)
    form.setFieldsValue({ name: '', subject_type: 'topic', aliases: '', geographic_contexts: '', exclusions: '' })
    setModal('create')
  }
  const openTerms = () => {
    if (!selected) return
    setEditing(selected)
    form.setFieldsValue({ name: selected.name, subject_type: selected.subject_type, aliases: join(selected.terms, 'alias'), geographic_contexts: join(selected.terms, 'geographic_context'), exclusions: join(selected.terms, 'exclusion') })
    setModal('terms')
  }
  const save = async (values: Values) => {
    setBusy(true)
    try {
      const common = {
        project_id: projectId, idempotency_key: crypto.randomUUID(), aliases: split(values.aliases),
        geographic_contexts: split(values.geographic_contexts), exclusions: split(values.exclusions),
      }
      if (modal === 'create') {
        const created = await backend.mutate_project_subject({ action: 'create_subject', name: values.name, subject_type: values.subject_type, ...common }) as { subject_id: string }
        onSelectSubject(created.subject_id)
      } else if (editing) {
        await backend.mutate_project_subject({ action: 'update_terms', subject_id: editing.id, ...common })
      }
      setModal(null)
      toast.success('主体规则已保存')
      await load()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '保存失败')
    } finally { setBusy(false) }
  }
  const correct = (item: Review, decision: Review['decision']) => {
    Modal.confirm({
      title: `将“${item.title}”标为${decisionName[decision]}？`,
      content: <Input.TextArea autoSize placeholder="说明依据，写入审计记录" id="subject-relevance-reason" />,
      okText: '保存人工判断', cancelText: '取消',
      onOk: async () => {
        const reason = (document.getElementById('subject-relevance-reason') as HTMLTextAreaElement | null)?.value || ''
        if (!reason.trim()) throw new Error('请填写判断依据')
        await backend.mutate_project_subject({
          project_id: projectId, action: 'correct_relevance', idempotency_key: crypto.randomUUID(),
          subject_id: item.subject_id, video_id: item.video_id, decision, reason,
        })
        toast.success('人工判断已记录')
        await load()
      },
    })
  }
  const review = data.review_queue.filter((item) => item.subject_id === selectedSubjectId)
  const scores = (data.top_scores || []).filter((item) => item.subject_id === selectedSubjectId)

  return <>
    {contextHolder}
    <Card className="subject-relevance-panel" title="研究主体与相关性规则" extra={<Space><Button onClick={load}>刷新</Button><Button type="primary" onClick={openCreate}>新建主体</Button></Space>}>
      <Alert type="info" showIcon message="规则只决定本项目的研究入口" description="原始公开视频仍保留在统一证据库；待判定和排除项不会进入该主体的 L1 评分或任务新候选，人工修正将留下审计记录。" />
      {error ? <Alert type="error" showIcon message={error} /> : null}
      <div className="subject-relevance-toolbar">
        <Select value={selectedSubjectId} onChange={onSelectSubject} placeholder="先选择或创建研究主体" options={data.subjects.map((subject) => ({ value: subject.id, label: `${subject.name} · ${typeNames[subject.subject_type] || subject.subject_type}` }))} />
        <Button disabled={!selected} onClick={openTerms}>编辑别名与边界</Button>
      </div>
      {selected ? <div className="subject-rule-summary">
        <strong>{selected.name}</strong><span>{typeNames[selected.subject_type] || selected.subject_type}</span>
        <small>别名：{join(selected.terms, 'alias') || '仅主体名称'}；地域：{join(selected.terms, 'geographic_context') || '不限制'}；排除：{join(selected.terms, 'exclusion') || '无'}</small>
        <div><Tag color="green">相关 {selected.relevant_count}</Tag><Tag color="gold">待判定 {selected.pending_count}</Tag><Tag>排除 {selected.irrelevant_count}</Tag></div>
      </div> : <p className="subject-empty">创建主体后，项目任务才能明确哪些候选值得进入决策。</p>}
    </Card>
    {selected ? <Card className="subject-score-card" title={`相关候选优先级 · ${selected.name}`} extra={<Space><span>最近一批评分 · 最多前 20 条</span>{onOpenVideoLibrary ? <Button size="small" onClick={onOpenVideoLibrary}>到视频库核对原始依据</Button> : null}</Space>}>
      <Alert type="info" showIcon message="批内相对排序，不是经营效果预测" description="只展示本项目本主体最近一批、当前仍相关的公开视频；不同运行批次的分数不能直接比较。低置信度或样本不足时先核对原始依据。评论、转写和大模型分析仍未在项目链路启用。" />
      <Table<ProjectScore> rowKey={(row) => `${row.source_run_id}:${row.video_id}`} pagination={{ pageSize: 8 }} dataSource={scores} locale={{ emptyText: '暂无可展示的项目级评分。运行主体绑定任务并核对相关性后再查看。' }} columns={[
        { title: '公开视频', key: 'video', render: (_, row) => <div><strong>{row.title}</strong><small>{row.account_name || '公开账号未提供名称'}</small></div> },
        { title: 'L1 优先级', dataIndex: 'score', key: 'score', render: (value: number) => <strong>{Number(value).toFixed(2)} / 100</strong> },
        { title: '数据置信度', dataIndex: 'confidence', key: 'confidence', render: (value?: string | null) => <Tag color={value === 'high' ? 'green' : value === 'medium' ? 'gold' : 'default'}>{value === 'high' ? '高' : value === 'medium' ? '中' : '低'}</Tag> },
        { title: '可复核版本', key: 'evidence', render: (_, row) => <small>{row.rule_version} · 运行 {row.source_run_id.slice(0, 8)} · 北京时间 {new Date(row.created_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })}</small> },
      ]} />
    </Card> : null}
    {selected ? <Card className="subject-review-card" title={`相关性审核 · ${selected.name}`} extra={<span>先看待判定与排除项；相关项已进入该主体决策队列</span>}>
      <Table<Review> rowKey={(row) => `${row.subject_id}:${row.video_id}`} pagination={{ pageSize: 8 }} dataSource={review} locale={{ emptyText: '暂时没有已判定候选。运行项目研究任务后会在这里出现。' }} columns={[
        { title: '候选公开内容', key: 'video', render: (_, row) => <div><strong>{row.title}</strong><small>{row.account_name || '公开账号未提供名称'}</small></div> },
        { title: '规则依据', key: 'evidence', render: (_, row) => <small>{row.match_detail?.invalidated_by === 'subject_terms_changed' ? '主体词已变更，等待重新判定' : <>命中：{[...(row.match_detail?.aliases || []), ...(row.match_detail?.geographic_contexts || [])].join('、') || '无'}{(row.match_detail?.exclusions || []).length ? `；排除：${row.match_detail?.exclusions?.join('、')}` : ''}</>}{row.run_id ? `；运行 ${row.run_id.slice(0, 8)}` : '；历史人工记录'}</small> },
        { title: '当前判断', key: 'decision', render: (_, row) => <Space direction="vertical" size={2}><Tag color={decisionColor[row.decision]}>{decisionName[row.decision]}{row.decision_source === 'manual' ? ' · 人工' : ' · 规则'}</Tag>{row.reviewed_by ? <small>{row.reviewed_by}</small> : null}</Space> },
        { title: '纠错', key: 'actions', render: (_, row) => <Space wrap><Button size="small" onClick={() => correct(row, 'relevant')}>标相关</Button><Button size="small" onClick={() => correct(row, 'pending')}>待判定</Button><Button size="small" danger onClick={() => correct(row, 'irrelevant')}>排除</Button></Space> },
      ]} />
    </Card> : null}
    <Modal open={modal !== null} title={modal === 'create' ? '创建研究主体' : `编辑“${editing?.name || ''}”的规则`} onCancel={() => setModal(null)} onOk={() => form.submit()} okText="保存" confirmLoading={busy} destroyOnClose>
      <Form form={form} layout="vertical" onFinish={save}>
        {modal === 'create' ? <><Form.Item name="name" label="主体名称" rules={[{ required: true, max: 160 }]}><Input placeholder="例如：渔岛温泉度假区" /></Form.Item><Form.Item name="subject_type" label="主体类型"><Select options={Object.entries(typeNames).map(([value, label]) => ({ value, label }))} /></Form.Item></> : null}
        <Form.Item name="aliases" label="别名 / 关键词"><Input.TextArea autoSize placeholder="用逗号或换行分隔；主体名称始终自动参与匹配" /></Form.Item>
        <Form.Item name="geographic_contexts" label="地域上下文（可选）"><Input.TextArea autoSize placeholder="填写后，候选需同时命中主体词和至少一个地域词才会自动相关" /></Form.Item>
        <Form.Item name="exclusions" label="排除词（可选）"><Input.TextArea autoSize placeholder="命中任一排除词即自动排除，优先级最高" /></Form.Item>
      </Form>
    </Modal>
  </>
}
