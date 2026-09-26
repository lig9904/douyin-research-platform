import React, { useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  message,
} from 'antd'
import { backend } from './backend'
import AppShell, { type ResearchView } from './AppShell'
import type { ProjectScope } from './src/projectScope'
import SubjectRelevancePanel, { type ProjectSubject } from './SubjectRelevancePanel'
import './research-briefs.css'
import './subject-profile.css'
import SubjectProfilePanel from './SubjectProfilePanel'

type BriefStatus = 'draft' | 'active' | 'paused'

type ResearchBrief = {
  id: string
  name: string
  platform: 'douyin'
  source_type: 'low_fan' | 'keyword' | 'account' | 'video_ids'
  target?: string | null
  time_window_hours: 0 | 24 | 72 | 168 | 720
  max_items: number
  depth: 'metadata' | 'comments' | 'media' | 'review_ready'
  cadence_hours?: 6 | 12 | 24 | null
  status: BriefStatus
  config_version: number
  next_due_at?: string | null
  last_dispatched_at?: string | null
  updated_at: string
  subject_id?: string | null
  subject_gate_status?: 'not_applicable' | 'ready' | 'subject_required'
}

type BriefRun = {
  id: string
  brief_id: string
  status: 'running' | 'success' | 'failed' | 'deferred'
  summary?: Record<string, unknown> | null
  error_code?: string | null
  started_at: string
  finished_at?: string | null
}

type FormValues = {
  name: string
  source_type: ResearchBrief['source_type']
  target?: string
  time_window_hours: ResearchBrief['time_window_hours']
  max_items: number
  depth: ResearchBrief['depth']
  cadence_hours: 0 | 6 | 12 | 24
  subject_id?: string
}

const sourceNames = {
  low_fan: '低粉高热榜',
  keyword: '关键词检索',
  account: '指定账号作品',
  video_ids: '指定视频 ID',
}

const depthNames = {
  metadata: '元数据与指标',
  comments: '加评论样本',
  media: '加私有媒体',
  review_ready: '准备人工审核材料',
}

const statusNames = {
  draft: '草稿',
  active: '运行中',
  paused: '已暂停',
}

const initialValues: FormValues = {
  name: '',
  source_type: 'keyword',
  target: '',
  time_window_hours: 72,
  max_items: 10,
  depth: 'comments',
  cadence_hours: 0,
}

function dateTime(value?: string | null) {
  if (!value) return '—'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function discoveryEstimate(source: FormValues['source_type']) {
  if (source === 'video_ids') return '按实际 ID 路由，最多 2 次详情请求；基础报价约 $0.001–$0.020 / 次'
  if (source === 'keyword') return '发现阶段最多约 $0.060 / 次'
  return '发现阶段最多约 $0.051 / 次'
}

function exactIds(value: string): string[] {
  const ids = value.trim().split(/[,\s]+/)
  if (ids.length < 1 || ids.length > 20 || new Set(ids).size !== ids.length ||
      ids.some((id) => !/^[0-9]{15,25}$/.test(id))) {
    throw new Error('请填写 1–20 个不同的纯数字抖音视频 ID，以逗号或换行分隔。')
  }
  return ids
}

export default function ResearchBriefs({
  onNavigate,
  scope,
}: {
  onNavigate: (view: ResearchView) => void
  scope: ProjectScope
}) {
  const projectId = scope.mode === 'project' ? scope.projectId : null
  const [form] = Form.useForm<FormValues>()
  const [toast, contextHolder] = message.useMessage()
  const [briefs, setBriefs] = useState<ResearchBrief[]>([])
  const [runs, setRuns] = useState<BriefRun[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [editingId, setEditingId] = useState('')
  const [projectSubjects, setProjectSubjects] = useState<ProjectSubject[]>([])
  const [selectedSubjectId, setSelectedSubjectId] = useState('')
  const requestVersion = useRef(0)
  const sourceType = Form.useWatch('source_type', form) || 'keyword'
  const depth = Form.useWatch('depth', form) || 'comments'
  const maxItemsLimit = sourceType === 'low_fan' || depth === 'media' || depth === 'review_ready' ? 5 : 20

  const load = async () => {
    const version = ++requestVersion.current
    setLoading(true)
    setError('')
    try {
      const result = (await backend.get_research_briefs(projectId ? { project_id: projectId } : {})) as {
        briefs: ResearchBrief[]
        runs: BriefRun[]
      }
      if (version === requestVersion.current) {
        setBriefs(result.briefs || [])
        setRuns(result.runs || [])
      }
    } catch (e) {
      if (version === requestVersion.current) setError(e instanceof Error ? e.message : String(e))
    } finally {
      if (version === requestVersion.current) setLoading(false)
    }
  }

  useEffect(() => {
    setBriefs([])
    setRuns([])
    setEditingId('')
    setProjectSubjects([])
    setSelectedSubjectId('')
    form.resetFields()
    if (projectId) form.setFieldValue('depth', 'metadata')
    void load()
    return () => { requestVersion.current += 1 }
  }, [projectId])

  const latestRuns = useMemo(() => {
    const result = new Map<string, BriefRun>()
    for (const run of runs) {
      if (!result.has(run.brief_id)) result.set(run.brief_id, run)
    }
    return result
  }, [runs])

  const mutate = async (action: string, briefId = '', values?: FormValues) => {
    const ids = values?.source_type === 'video_ids' ? exactIds(values.target || '') : []
    return backend.mutate_research_brief({
      action,
      idempotency_key: crypto.randomUUID(),
      brief_id: briefId,
      name: values?.name || '',
      platform: 'douyin',
      source_type: values?.source_type || 'low_fan',
      target: values?.source_type === 'low_fan' ? '' : ids.length ? ids.join(',') : (values?.target || ''),
      time_window_hours: ids.length ? 0 : (values?.time_window_hours ?? 24),
      max_items: ids.length || values?.max_items || 5,
      depth: values?.depth || 'metadata',
      cadence_hours: ids.length ? null : (values?.cadence_hours || null),
      ...(projectId ? { subject_id: values?.subject_id || '' } : {}),
      ...(projectId ? { project_id: projectId } : {}),
    })
  }

  const save = async (values: FormValues) => {
    if (values.source_type === 'video_ids') {
      try { exactIds(values.target || '') } catch (e) {
        toast.error(e instanceof Error ? e.message : '视频 ID 无效')
        return
      }
    }
    if (projectId && values.depth !== 'metadata') {
      toast.error('项目任务目前只支持元数据采集。')
      return
    }
    if (projectId && !values.subject_id) {
      toast.error('请先创建或选择研究主体。')
      return
    }
    setSaving(true)
    try {
      await mutate(editingId ? 'update' : 'create', editingId, values)
      toast.success(editingId ? '研究任务已更新' : '研究任务草稿已保存')
      setEditingId('')
      form.resetFields()
      await load()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const changeState = async (action: 'activate' | 'pause' | 'archive', brief: ResearchBrief) => {
    try {
      await mutate(action, brief.id)
      toast.success(action === 'activate' ? '任务已激活，将由调度器执行' : '任务状态已更新')
      await load()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '状态更新失败')
    }
  }

  const confirmActivate = (brief: ResearchBrief) => {
    Modal.confirm({
      title: `激活“${brief.name}”？`,
      content: (
        <div className="activation-copy">
          <p>激活后会按任务范围调用 TikHub 并写入研究库；首次任务将尽快执行。</p>
          <p><strong>{discoveryEstimate(brief.source_type)}</strong>，缓存命中或没有新候选时实际费用可能更低。</p>
          <p>音频转写和 L3 分析不会自动提交，仍须在内容页单独人工审核。</p>
        </div>
      ),
      okText: '确认激活',
      cancelText: '取消',
      onOk: () => changeState('activate', brief),
    })
  }

  const confirmArchive = (brief: ResearchBrief) => {
    Modal.confirm({
      title: `归档“${brief.name}”？`,
      content: '归档后任务不再调度，也不会出现在当前任务列表；已采集的研究资产和运行账本不会删除。',
      okText: '确认归档',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: () => changeState('archive', brief),
    })
  }

  const edit = (brief: ResearchBrief) => {
    setEditingId(brief.id)
    if (brief.subject_id) setSelectedSubjectId(brief.subject_id)
    form.setFieldsValue({
      name: brief.name,
      source_type: brief.source_type,
      target: brief.target || '',
      time_window_hours: brief.time_window_hours,
      max_items: brief.max_items,
      depth: brief.depth,
      cadence_hours: brief.cadence_hours || 0,
      subject_id: brief.subject_id || '',
    })
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  return (
    <AppShell
      activeView="briefs"
      onNavigate={onNavigate}
      title="研究任务"
      subtitle="把你的研究问题变成可追踪、可暂停、可核算的采集任务"
      mainClassName="brief-page"
      actions={<Button onClick={load}>刷新状态</Button>}
    >
      {contextHolder}
      <Alert
        className="brief-boundary"
        type="info"
        showIcon
        message={projectId ? '当前项目任务只采公开元数据' : '任务负责确定研究范围，不替你作结论'}
        description={projectId
          ? '采集会生成当前项目的候选视频，不等于已确认为可比的研究依据；项目任务暂不采新评论或媒体。需要 ASR 或 L3 时，先核对实际内容并接受视频，再受控入库媒体、逐条试听与审核。'
          : '系统按来源、时间窗和深度采集并合并到现有视频、账号、热点资产；ASR 与 L3 始终保留独立人工审核。'}
      />
      {error && <Alert type="error" showIcon message="研究任务加载失败" description={error} />}

      {projectId ? <SubjectRelevancePanel
        projectId={projectId}
        selectedSubjectId={selectedSubjectId}
        onSelectSubject={(id) => {
          setSelectedSubjectId(id)
          if (!editingId) form.setFieldValue('subject_id', id)
        }}
        onSubjectsChange={setProjectSubjects}
        onOpenVideoLibrary={() => onNavigate('videos')}
      /> : null}

      {projectId && selectedSubjectId ? <SubjectProfilePanel
        key={`${projectId}:${selectedSubjectId}`}
        projectId={projectId}
        subjectId={selectedSubjectId}
        subjectName={projectSubjects.find((subject) => subject.id === selectedSubjectId)?.name}
        subjectType={projectSubjects.find((subject) => subject.id === selectedSubjectId)?.subject_type}
      /> : null}

      <div className="brief-layout">
        <Card className="brief-editor" title={editingId ? '编辑研究任务' : '新建研究任务'}>
          <Form<FormValues>
            form={form}
            layout="vertical"
            initialValues={{ ...initialValues, depth: projectId ? 'metadata' : initialValues.depth }}
            onFinish={save}
          >
            <Form.Item name="name" label="任务名称" rules={[{ required: true, max: 80 }]}>
              <Input placeholder="例如：神话文旅内容机会" />
            </Form.Item>
            {projectId ? <Form.Item name="subject_id" label="研究主体" rules={[{ required: true, message: '请选择研究主体' }]} extra="只让“相关”候选进入本项目本主体的 L1 排序与决策队列；待判定、排除项保留在审核区。L1 不是经营效果预测。">
              <Select placeholder="先在上方创建或选择主体" options={projectSubjects.map((subject) => ({ value: subject.id, label: `${subject.name} · ${subject.subject_type}` }))} />
            </Form.Item> : null}
            <div className="brief-form-grid">
              <Form.Item name="source_type" label="从哪里找" rules={[{ required: true }]}>
                <Select
                  options={Object.entries(sourceNames)
                    .filter(([value]) => projectId || value !== 'video_ids')
                    .map(([value, label]) => ({ value, label }))}
                  onChange={(value) => {
                    if (value === 'low_fan') {
                      form.setFieldsValue({ max_items: 5, time_window_hours: 72, target: '' })
                    } else if (value === 'video_ids') {
                      form.setFieldsValue({ max_items: 1, time_window_hours: 0, depth: 'metadata', cadence_hours: 0, target: '' })
                    } else if (form.getFieldValue('time_window_hours') === 0) {
                      form.setFieldsValue({ time_window_hours: 72, target: '' })
                    }
                  }}
                />
              </Form.Item>
              {sourceType !== 'low_fan' && (
                <Form.Item
                  name="target"
                  label={sourceType === 'keyword' ? '关键词' : sourceType === 'video_ids' ? '抖音视频 ID' : '账号 sec_user_id'}
                  rules={[{ required: true, max: sourceType === 'video_ids' ? 519 : 120 }]}
                >
                  {sourceType === 'video_ids'
                    ? <Input.TextArea rows={3} placeholder="每行一个视频 ID，或用逗号分隔；最多 20 条，不按发布日期过滤" />
                    : <Input placeholder={sourceType === 'keyword' ? '输入主题、事件或产品词' : '输入公开账号 sec_user_id'} />}
                </Form.Item>
              )}
              {sourceType !== 'video_ids' && <Form.Item name="time_window_hours" label="研究时间范围">
                <Select
                  options={[
                    { value: 24, label: '近24小时' },
                    { value: 72, label: '近3天' },
                    { value: 168, label: '近7天' },
                    { value: 720, label: '近30天', disabled: sourceType === 'low_fan' },
                  ]}
                />
              </Form.Item>}
              {sourceType !== 'video_ids' && <Form.Item name="max_items" label="每次最多候选">
                <InputNumber min={1} max={maxItemsLimit} precision={0} />
              </Form.Item>}
              <Form.Item name="depth" label="采集深度">
                <Select
                  options={Object.entries(depthNames)
                    .filter(([value]) => !projectId || value === 'metadata')
                    .map(([value, label]) => ({ value, label }))}
                  disabled={Boolean(projectId)}
                  onChange={(value) => {
                    if ((value === 'media' || value === 'review_ready') && Number(form.getFieldValue('max_items')) > 5) {
                      form.setFieldValue('max_items', 5)
                    }
                  }}
                />
              </Form.Item>
              {sourceType !== 'video_ids' && <Form.Item name="cadence_hours" label="执行频率">
                <Select
                  options={[
                    { value: 0, label: '只执行一次' },
                    { value: 6, label: '每6小时' },
                    { value: 12, label: '每12小时' },
                    { value: 24, label: '每天' },
                  ]}
                />
              </Form.Item>}
            </div>
            <div className="brief-cost-note">
              <strong>{discoveryEstimate(sourceType)}</strong>
              <span>{projectId ? '只执行公开元数据采集；当前尚无项目级成本分摊。' : '评论按实际新候选另计；所有调用汇总到运行与成本页。'}</span>
            </div>
            <Space>
              <Button type="primary" htmlType="submit" loading={saving}>
                {editingId ? '保存修改' : '保存草稿'}
              </Button>
              {editingId && (
                <Button onClick={() => { setEditingId(''); form.resetFields() }}>取消编辑</Button>
              )}
            </Space>
          </Form>
        </Card>

        <Card className="brief-guide" title="系统会怎样执行">
          <ol>
            <li><b>发现</b><span>按指定来源抓取有限候选并补齐详情。</span></li>
            <li><b>合并</b><span>同一视频和账号进入统一资产，不制造重复记录。</span></li>
            <li><b>加深</b><span>{projectId ? '项目任务暂不采新评论或私有媒体；已保存的公开评论仅作阅读依据。' : '按深度采评论或私有媒体；无需的步骤直接停止。'}</span></li>
            <li><b>人工判断</b><span>{projectId ? '先判断候选与项目目标是否可比；只有确认纳入的视频，才能在视频库继续逐条试听音频、审核待发送正文。' : '需要转写或大模型分析时，再在内容页确认。'}</span></li>
          </ol>
        </Card>
      </div>

      <Card className="brief-table-card" title={`我的研究任务 · ${briefs.length}`}>
        <Spin spinning={loading}>
          <Table<ResearchBrief>
            rowKey="id"
            dataSource={briefs}
            pagination={false}
            locale={{ emptyText: '还没有研究任务，先在上方保存一个草稿。' }}
            columns={[
              {
                title: '研究范围',
                key: 'scope',
                render: (_, row) => (
                  <div className="brief-scope">
                    <strong>{row.name}</strong>
                    <span>{sourceNames[row.source_type]}{row.target ? ` · ${row.target}` : ''}</span>
                    <small>{row.source_type === 'video_ids' ? '不限发布日期' : `近${row.time_window_hours}小时`} · 最多{row.max_items}条 · {depthNames[row.depth]}</small>
                    {projectId && row.subject_gate_status === 'subject_required' ? <Tag color="warning">需绑定主体后才能激活</Tag> : null}
                  </div>
                ),
              },
              {
                title: '状态',
                key: 'status',
                width: 210,
                render: (_, row) => {
                  const run = latestRuns.get(row.id)
                  return (
                    <div className="brief-status">
                      <Tag color={row.status === 'active' ? 'processing' : row.status === 'draft' ? 'default' : 'gold'}>
                        {statusNames[row.status]}
                      </Tag>
                      <span>下次：{dateTime(row.next_due_at)}</span>
                      <small>最近：{run ? `${run.status} · ${dateTime(run.started_at)}` : '尚未执行'}</small>
                    </div>
                  )
                },
              },
              {
                title: '频率',
                dataIndex: 'cadence_hours',
                width: 100,
                render: (value) => value ? `每${value}小时` : '单次',
              },
              {
                title: '操作',
                key: 'actions',
                width: 250,
                render: (_, row) => (
                  <Space wrap>
                    {row.status !== 'active' && <Button size="small" onClick={() => edit(row)}>编辑</Button>}
                    {row.status !== 'active' && <Button size="small" type="primary" onClick={() => confirmActivate(row)}>激活</Button>}
                    {row.status === 'active' && <Button size="small" onClick={() => changeState('pause', row)}>暂停</Button>}
                    <Button size="small" danger onClick={() => confirmArchive(row)}>归档</Button>
                  </Space>
                ),
              },
            ]}
          />
        </Spin>
      </Card>
    </AppShell>
  )
}
