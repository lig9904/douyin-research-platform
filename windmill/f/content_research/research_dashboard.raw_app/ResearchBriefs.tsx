import React, { useEffect, useMemo, useState } from 'react'
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
import './research-briefs.css'

type BriefStatus = 'draft' | 'active' | 'paused'

type ResearchBrief = {
  id: string
  name: string
  platform: 'douyin'
  source_type: 'low_fan' | 'keyword' | 'account'
  target?: string | null
  time_window_hours: 24 | 72 | 168 | 720
  max_items: number
  depth: 'metadata' | 'comments' | 'media' | 'review_ready'
  cadence_hours?: 6 | 12 | 24 | null
  status: BriefStatus
  config_version: number
  next_due_at?: string | null
  last_dispatched_at?: string | null
  updated_at: string
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
}

const sourceNames = {
  low_fan: '低粉高热榜',
  keyword: '关键词检索',
  account: '指定账号作品',
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
  if (source === 'keyword') return '发现阶段最多约 $0.060 / 次'
  return '发现阶段最多约 $0.051 / 次'
}

export default function ResearchBriefs({
  onNavigate,
}: {
  onNavigate: (view: ResearchView) => void
}) {
  const [form] = Form.useForm<FormValues>()
  const [toast, contextHolder] = message.useMessage()
  const [briefs, setBriefs] = useState<ResearchBrief[]>([])
  const [runs, setRuns] = useState<BriefRun[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [editingId, setEditingId] = useState('')
  const sourceType = Form.useWatch('source_type', form) || 'keyword'
  const depth = Form.useWatch('depth', form) || 'comments'
  const maxItemsLimit = sourceType === 'low_fan' || depth === 'media' || depth === 'review_ready' ? 5 : 20

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const result = (await backend.get_research_briefs({})) as {
        briefs: ResearchBrief[]
        runs: BriefRun[]
      }
      setBriefs(result.briefs || [])
      setRuns(result.runs || [])
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  const latestRuns = useMemo(() => {
    const result = new Map<string, BriefRun>()
    for (const run of runs) {
      if (!result.has(run.brief_id)) result.set(run.brief_id, run)
    }
    return result
  }, [runs])

  const mutate = async (action: string, briefId = '', values?: FormValues) => {
    return backend.mutate_research_brief({
      action,
      idempotency_key: crypto.randomUUID(),
      brief_id: briefId,
      name: values?.name || '',
      platform: 'douyin',
      source_type: values?.source_type || 'low_fan',
      target: values?.source_type === 'low_fan' ? '' : (values?.target || ''),
      time_window_hours: values?.time_window_hours || 24,
      max_items: values?.max_items || 5,
      depth: values?.depth || 'metadata',
      cadence_hours: values?.cadence_hours || null,
    })
  }

  const save = async (values: FormValues) => {
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
    form.setFieldsValue({
      name: brief.name,
      source_type: brief.source_type,
      target: brief.target || '',
      time_window_hours: brief.time_window_hours,
      max_items: brief.max_items,
      depth: brief.depth,
      cadence_hours: brief.cadence_hours || 0,
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
        message="任务负责确定研究范围，不替你作结论"
        description="系统按来源、时间窗和深度采集并合并到现有视频、账号、热点资产；ASR 与 L3 始终保留独立人工审核。"
      />
      {error && <Alert type="error" showIcon message="研究任务加载失败" description={error} />}

      <div className="brief-layout">
        <Card className="brief-editor" title={editingId ? '编辑研究任务' : '新建研究任务'}>
          <Form<FormValues>
            form={form}
            layout="vertical"
            initialValues={initialValues}
            onFinish={save}
          >
            <Form.Item name="name" label="任务名称" rules={[{ required: true, max: 80 }]}>
              <Input placeholder="例如：神话文旅内容机会" />
            </Form.Item>
            <div className="brief-form-grid">
              <Form.Item name="source_type" label="从哪里找" rules={[{ required: true }]}>
                <Select
                  options={Object.entries(sourceNames).map(([value, label]) => ({ value, label }))}
                  onChange={(value) => {
                    if (value === 'low_fan') {
                      form.setFieldsValue({ max_items: 5, time_window_hours: 72, target: '' })
                    }
                  }}
                />
              </Form.Item>
              {sourceType !== 'low_fan' && (
                <Form.Item
                  name="target"
                  label={sourceType === 'keyword' ? '关键词' : '账号 sec_user_id'}
                  rules={[{ required: true, max: 120 }]}
                >
                  <Input placeholder={sourceType === 'keyword' ? '输入主题、事件或产品词' : '输入公开账号 sec_user_id'} />
                </Form.Item>
              )}
              <Form.Item name="time_window_hours" label="研究时间范围">
                <Select
                  options={[
                    { value: 24, label: '近24小时' },
                    { value: 72, label: '近3天' },
                    { value: 168, label: '近7天' },
                    { value: 720, label: '近30天', disabled: sourceType === 'low_fan' },
                  ]}
                />
              </Form.Item>
              <Form.Item name="max_items" label="每次最多候选">
                <InputNumber min={1} max={maxItemsLimit} precision={0} />
              </Form.Item>
              <Form.Item name="depth" label="采集深度">
                <Select
                  options={Object.entries(depthNames).map(([value, label]) => ({ value, label }))}
                  onChange={(value) => {
                    if ((value === 'media' || value === 'review_ready') && Number(form.getFieldValue('max_items')) > 5) {
                      form.setFieldValue('max_items', 5)
                    }
                  }}
                />
              </Form.Item>
              <Form.Item name="cadence_hours" label="执行频率">
                <Select
                  options={[
                    { value: 0, label: '只执行一次' },
                    { value: 6, label: '每6小时' },
                    { value: 12, label: '每12小时' },
                    { value: 24, label: '每天' },
                  ]}
                />
              </Form.Item>
            </div>
            <div className="brief-cost-note">
              <strong>{discoveryEstimate(sourceType)}</strong>
              <span>评论按实际新候选另计；所有调用汇总到运行与成本页。</span>
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
            <li><b>加深</b><span>按深度采评论或私有媒体；无需的步骤直接停止。</span></li>
            <li><b>人工判断</b><span>需要转写或大模型分析时，再在内容页确认。</span></li>
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
                    <small>近{row.time_window_hours}小时 · 最多{row.max_items}条 · {depthNames[row.depth]}</small>
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
