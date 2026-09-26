import React, { useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert,
  Button,
  Checkbox,
  Input,
  Modal,
  Pagination,
  Select,
  Spin,
  Tag,
  Tooltip,
} from 'antd'
import { backend } from './backend'
import AppShell, { type ResearchView } from './AppShell'
import L3ReviewPanel from './src/components/L3ReviewPanel'
import ASRMediaReviewPanel from './src/components/ASRMediaReviewPanel'
import ProjectASRMediaReviewPanel from './src/components/ProjectASRMediaReviewPanel'
import ProjectL3ReviewPanel from './src/components/ProjectL3ReviewPanel'
import VideoMediaPreview from './src/components/VideoMediaPreview'
import ASRTranscriptPanel, { type ASRTranscript } from './src/components/ASRTranscriptPanel'
import MetricTimeline from './src/components/MetricTimeline'
import RawRecordPanel from './src/components/RawRecordPanel'
import PlatformIcon from './src/components/PlatformIcon'
import ProjectVideoEvidence from './ProjectVideoEvidence'
import ProjectCaseReviewPanel from './ProjectCaseReviewPanel'
import { useProjectScope, type ProjectScope } from './src/projectScope'
import { formatPlayInteractionRate } from './src/playInteractionRate'
import {
  getResearchUserState,
  mutateResearchState,
  type ResearchUserState,
} from './src/components/ResearchActions'
import './video-library.css'

type Platform = {
  key: string
  name: string
  enabled: boolean
  provider_status: string
}

type VideoItem = {
  id: string
  platform: string
  platform_video_id: string
  title: string
  description?: string | null
  source_url?: string | null
  published_at?: string | null
  duration_ms?: number | null
  research_level: number | null
  monitoring_status: string | null
  account_id?: string | null
  account_name?: string | null
  play_count?: number | null
  like_count?: number | null
  comment_count?: number | null
  share_count?: number | null
  collect_count?: number | null
  author_follower_count?: number | null
  metric_captured_at?: string | null
  metric_source_kind?: 'merged' | 'billboard' | 'detail' | 'other' | null
  metric_provenance?: Record<string, { source_kind: string; captured_at: string }>
  priority: number | null
  sources: string[]
  source_count: number
  collection_count: number | null
  project_inclusion_status?: 'candidate' | 'shortlisted' | 'accepted' | 'rejected' | null
  evidence?: {
    source_type: string
    source_key?: string | null
    discovered_at?: string | null
    rank_value?: number | null
  }[]
  comments?: {
    platform_comment_id?: string | null
    text_content?: string | null
    like_count?: number | null
    published_at?: string | null
  }[]
  comment_features?: {
    feature_version: string
    calculated_at: string
    sampled_comment_count: number
    source_observation_count: number
    eligible_text_count: number | null
    question_text_count: number
    duplicate_text_count: number | null
  } | null
  l3_analysis?: L3Analysis | null
  asr_transcript?: ASRTranscript | null
}

type L3Analysis = {
  analysis_type: string
  model?: string | null
  model_revision?: string | null
  prompt_version?: string | null
  schema_version?: string | null
  created_at?: string | null
  output: {
    narrative_structure?: string[]
    hook_functions?: string[]
    comment_semantics?: string[]
    case_comparisons?: string[]
    mechanism_hypotheses?: string[]
    ip_fit?: string[]
    limitations?: string[]
    mechanism_hypotheses_are_inferences?: boolean
    privacy_reviewed?: boolean
  }
  cost: {
    api_cost?: number | null
    asr_cost?: number | null
    llm_cost?: number | null
    total_cost?: number | null
    currency?: string | null
    basis?: string | null
  }
}

type VideoLibraryData = {
  platforms: Platform[]
  source_options: { value: string }[]
  total: number
  page: number
  page_size: number
  items: VideoItem[]
  detail: VideoItem | Record<string, never>
}

type Filters = {
  platform: string
  days: number
  research_level: number
  source_type: string
  priority_min: number
  status: string
  play_min: number
  play_max: number
  follower_min: number
  follower_max: number
  collected: string
  query: string
  sort: string
  page: number
  page_size: number
}

const sourceLabels: Record<string, string> = {
  low_fan: '低粉高赞',
  search: '搜索发现',
  creator_material: '创作榜',
  creator: '创作榜',
  watched_account: '对标账号',
  detail_enrichment: '详情补全',
}

const statusLabels: Record<string, string> = {
  observe: '观察中',
  follow_up: '待跟进',
  selected: '已入选',
  stopped: '已停止',
}

function formatCount(v?: number | null) {
  if (v === null || v === undefined) return '—'
  if (v >= 100000000) return `${(v / 100000000).toFixed(1)}亿`
  if (v >= 10000) return `${(v / 10000).toFixed(1)}万`
  return Number(v).toLocaleString('zh-CN')
}

function formatFollowerCount(v?: number | null) {
  return v === 0 ? '0 · 待核' : formatCount(v)
}

const zeroFollowerExplanation = '上游接口返回账号粉丝 0，尚不能据此认定真实为零；请核对账号主页或独立账号接口，以及采集时间。'

function hasContradictoryZeroPlay(item: VideoItem) {
  return item.play_count === 0 && [
    item.like_count, item.comment_count, item.share_count, item.collect_count,
  ].some(value => typeof value === 'number' && value > 0)
}

function projectInclusionLabel(status?: VideoItem['project_inclusion_status']) {
  switch (status) {
    case 'accepted': return '已接受'
    case 'shortlisted': return '已预选'
    case 'candidate': return '待核候选'
    case 'rejected': return '未采纳'
    default: return '待核'
  }
}

function formatDate(v?: string | null) {
  if (!v) return '—'
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(d)
}

function formatFullDate(v?: string | null) {
  if (!v) return '—'
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(d)
}

function formatCost(value?: number | null, currency?: string | null) {
  if (value === null || value === undefined) return '未知'
  return `${Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 6 })} ${currency || ''}`.trim()
}

function priorityText(v: number) {
  if (v >= 80) return '高'
  if (v >= 60) return '中'
  return '低'
}

function priorityTone(v: number) {
  if (v >= 80) return 'high'
  if (v >= 60) return 'medium'
  return 'low'
}

function sourceName(v: string) {
  return sourceLabels[v] || v
}

function statusName(v?: string | null) {
  return statusLabels[v || ''] || v || '观察中'
}

function statusColor(v?: string | null) {
  if (v === 'selected') return 'green'
  if (v === 'follow_up') return 'blue'
  if (v === 'stopped') return 'default'
  return 'orange'
}

function toNum(v: string) {
  const trimmed = v.trim()
  if (!trimmed) return -1
  const n = Number(trimmed)
  return Number.isFinite(n) ? n : -1
}

const initialFilters: Filters = {
  platform: 'douyin',
  days: 30,
  research_level: -1,
  source_type: 'all',
  priority_min: -1,
  status: 'all',
  play_min: -1,
  play_max: -1,
  follower_min: -1,
  follower_max: -1,
  collected: 'all',
  query: '',
  sort: 'published_desc',
  page: 1,
  page_size: 10,
}

export default function VideoLibrary({
  onNavigate,
  scope,
  initialSelectedVideoId = '',
}: {
  onNavigate: (view: ResearchView) => void
  scope: ProjectScope
  initialSelectedVideoId?: string
}) {
  const isProject = scope.mode === 'project'
  const projectId = isProject ? scope.projectId : undefined
  const { projects } = useProjectScope()
  const currentProject = projects.find(item => item.id === projectId)
  const projectRole = currentProject?.member_role
  const canReviewProject = projectRole === 'owner' || projectRole === 'admin'
  const canReviewProjectL3 = canReviewProject && currentProject?.can_review_l3 === true
  const [filters, setFilters] = useState<Filters>(initialFilters)
  const [draft, setDraft] = useState<Filters>(initialFilters)
  const [data, setData] = useState<VideoLibraryData | null>(null)
  const [selectedVideoId, setSelectedVideoId] = useState(initialSelectedVideoId)
  const [selectedRows, setSelectedRows] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [writeBusy, setWriteBusy] = useState(false)
  const [writeNotice, setWriteNotice] = useState('')
  const [writeError, setWriteError] = useState('')
  const [userState, setUserState] = useState<ResearchUserState | null>(null)
  const [collectionModalOpen, setCollectionModalOpen] = useState(false)
  const [collectionName, setCollectionName] = useState('')
  const [collectionTargetIds, setCollectionTargetIds] = useState<string[]>([])
  const [filterModalOpen, setFilterModalOpen] = useState(false)
  const [filterName, setFilterName] = useState('')
  const requestVersion = useRef(0)

  const load = async (next: Filters, selected = selectedVideoId) => {
    const version = ++requestVersion.current
    setLoading(true)
    setError('')
    setData(null)
    try {
      const result = (await backend.get_video_library({
        ...next,
        selected_video_id: selected,
        ...(projectId ? { project_id: projectId } : {}),
      })) as VideoLibraryData
      if (version !== requestVersion.current) return
      setData(result)
      if (!selected && result.items.length) {
        setSelectedVideoId(result.items[0].id)
      }
      if (selected && result.detail && 'id' in result.detail) {
        setSelectedVideoId(String(result.detail.id || selected))
      }
    } catch (e) {
      if (version === requestVersion.current) setError(e instanceof Error ? e.message : String(e))
    } finally {
      if (version === requestVersion.current) setLoading(false)
    }
  }

  useEffect(() => {
    void load(filters, selectedVideoId)
    return () => { requestVersion.current += 1 }
  }, [filters, projectId])

  useEffect(() => {
    setSelectedVideoId('')
    setSelectedRows(new Set())
    setData(null)
    setError('')
  }, [projectId])

  const loadUserState = async () => {
    try {
      setUserState(await getResearchUserState('videos'))
    } catch (e) {
      setWriteError(e instanceof Error ? e.message : String(e))
    }
  }

  useEffect(() => {
    if (!isProject) void loadUserState()
    else setUserState(null)
  }, [isProject])

  const platforms = [
    { key: 'all', name: '全部平台', enabled: true, provider_status: 'aggregate' },
    ...(data?.platforms || []),
  ]

  const detail = data?.detail && 'id' in data.detail ? (data.detail as VideoItem) : null

  const totalPages = useMemo(
    () => Math.max(1, Math.ceil((data?.total || 0) / Math.max(1, filters.page_size))),
    [data?.total, filters.page_size],
  )

  const applyFilters = () => {
    const next = { ...draft, page: 1 }
    setSelectedVideoId('')
    setSelectedRows(new Set())
    setFilters(next)
  }

  const resetFilters = () => {
    setDraft(initialFilters)
    setSelectedVideoId('')
    setSelectedRows(new Set())
    setFilters(initialFilters)
  }

  const selectPlatform = (platform: string) => {
    const next = { ...draft, platform, page: 1 }
    setDraft(next)
    setSelectedVideoId('')
    setSelectedRows(new Set())
    setFilters(next)
  }

  const selectDetail = async (id: string) => {
    setSelectedVideoId(id)
    await load(filters, id)
  }

  const showAcceptedVideo = (id: string) => {
    const next = { ...initialFilters, days: 365 }
    setSelectedVideoId(id)
    setDraft(next)
    setFilters(next)
  }

  const toggleRow = (id: string, checked: boolean) => {
    const next = new Set(selectedRows)
    if (checked) next.add(id)
    else next.delete(id)
    setSelectedRows(next)
  }

  const runWrite = async (
    input: Parameters<typeof mutateResearchState>[0],
    success: string,
  ) => {
    setWriteBusy(true)
    setWriteError('')
    setWriteNotice('')
    try {
      await mutateResearchState(input)
      setWriteNotice(success)
      await Promise.all([load(filters, selectedVideoId), loadUserState()])
    } catch (e) {
      setWriteError(e instanceof Error ? e.message : String(e))
    } finally {
      setWriteBusy(false)
    }
  }

  const openCollection = (ids: string[]) => {
    setCollectionTargetIds(ids)
    setCollectionName(userState?.collections[0]?.name || '')
    setCollectionModalOpen(true)
  }

  const saveCollection = async () => {
    const name = collectionName.trim()
    if (!name || !collectionTargetIds.length) return
    await runWrite(
      {
        action: 'add_to_collection',
        asset_type: 'video',
        asset_ids: collectionTargetIds,
        collection_name: name,
      },
      `已加入专题「${name}」。`,
    )
    setCollectionModalOpen(false)
  }

  const saveCurrentFilter = async () => {
    const name = filterName.trim()
    if (!name) return
    const { page: _page, ...persisted } = filters
    await runWrite(
      {
        action: 'save_filter',
        view_key: 'videos',
        filter_name: name,
        filters_json: JSON.stringify(persisted),
      },
      `筛选「${name}」已保存。`,
    )
    setFilterModalOpen(false)
  }

  const applySavedFilter = (name: string) => {
    const saved = userState?.saved_filters.find((item) => item.name === name)
    if (!saved) return
    const next = { ...initialFilters, ...saved.filters, page: 1 } as Filters
    setDraft(next)
    setFilters(next)
    setSelectedRows(new Set())
    setSelectedVideoId('')
    setWriteNotice(`已应用筛选「${name}」。`)
  }

  return (
    <AppShell
      activeView="videos"
      onNavigate={onNavigate}
      title="视频库"
      subtitle={isProject ? `项目范围：${scope.projectName} · 含采集候选；逐条核对实际内容后才算研究依据` : '多平台视频资产 / 黑马候选 / 研究流转'}
      mainClassName="video-library-main"
      headerClassName="video-library-topbar"
      actions={
        <>
          <Input.Search
            className="global-search"
            placeholder="搜索视频标题、账号名称、关键词..."
            value={draft.query}
            onChange={(e) => setDraft({ ...draft, query: e.target.value })}
            onSearch={applyFilters}
            allowClear
          />
          <Select
            value={draft.days}
            onChange={(days) => setDraft({ ...draft, days })}
            options={[
              { value: 7, label: '近7天' },
              { value: 30, label: '近30天' },
              { value: 90, label: '近90天' },
              ...(isProject ? [{ value: 365, label: '近一年' }] : []),
            ]}
          />
          {!isProject && !!userState?.saved_filters.length && (
            <Select
              placeholder="已保存筛选"
              onChange={applySavedFilter}
              options={userState.saved_filters.map((item) => ({
                value: item.name,
                label: item.name,
              }))}
            />
          )}
          {!isProject && <Button type="primary" ghost onClick={() => setFilterModalOpen(true)}>
              保存筛选
            </Button>}
          {!isProject && <Button type="primary" disabled>导出结果</Button>}
        </>
      }
    >
          {projectId ? <ProjectVideoEvidence key={projectId} projectId={projectId} onAccepted={showAcceptedVideo} /> : null}
          {!isProject && writeNotice && (
            <Alert type="success" showIcon message={writeNotice} closable onClose={() => setWriteNotice('')} />
          )}
          {!isProject && writeError && (
            <Alert type="error" showIcon message="写操作失败" description={writeError} closable onClose={() => setWriteError('')} />
          )}
          <section className="platform-strip card">
            <div className="platform-title">平台筛选</div>
            <div className="platform-tabs">
              {platforms.map((p) => (
                <Tooltip
                  key={p.key}
                  title={!p.enabled && p.key !== 'all' ? '已预留，数据源尚未接入' : ''}
                >
                  <button
                    className={filters.platform === p.key ? 'platform-tab selected' : 'platform-tab'}
                    onClick={() => selectPlatform(p.key)}
                  >
                    <PlatformIcon platform={p.key} className="platform-logo" />
                    {p.name}
                    {!p.enabled && <i className="planned-dot" />}
                  </button>
                </Tooltip>
              ))}
            </div>
          </section>

          <section className="video-filter-card card">
            <div className="video-filter-grid">
              {!isProject && <label>
                <span>时间范围</span>
                <Select
                  value={draft.days}
                  onChange={(days) => setDraft({ ...draft, days })}
                  options={[
                    { value: 7, label: '近7天' },
                    { value: 30, label: '近30天' },
                    { value: 90, label: '近90天' },
                  ]}
                />
              </label>}
              {!isProject && <label>
                <span>研究层级</span>
                <Select
                  value={draft.research_level}
                  onChange={(research_level) => setDraft({ ...draft, research_level })}
                  options={[
                    { value: -1, label: '全部' },
                    { value: 0, label: 'L0' },
                    { value: 1, label: 'L1' },
                    { value: 2, label: 'L2' },
                    { value: 3, label: 'L3' },
                  ]}
                />
              </label>}
              {!isProject && <label>
                <span>来源类型</span>
                <Select
                  value={draft.source_type}
                  onChange={(source_type) => setDraft({ ...draft, source_type })}
                  options={[
                    { value: 'all', label: '全部' },
                    ...(data?.source_options || []).map((x) => ({
                      value: x.value,
                      label: sourceName(x.value),
                    })),
                  ]}
                />
              </label>}
              {!isProject && <label>
                <span>优先级分</span>
                <Select
                  value={draft.priority_min}
                  onChange={(priority_min) => setDraft({ ...draft, priority_min })}
                  options={[
                    { value: -1, label: '全部' },
                    { value: 80, label: '80分以上' },
                    { value: 60, label: '60分以上' },
                    { value: 40, label: '40分以上' },
                  ]}
                />
              </label>}
              {!isProject && <label>
                <span>状态</span>
                <Select
                  value={draft.status}
                  onChange={(status) => setDraft({ ...draft, status })}
                  options={[
                    { value: 'all', label: '全部' },
                    { value: 'observe', label: '观察中' },
                    { value: 'follow_up', label: '待跟进' },
                    { value: 'selected', label: '已入选' },
                    { value: 'stopped', label: '已停止' },
                  ]}
                />
              </label>}

              <label>
                <span>播放量</span>
                <div className="range-inputs">
                  <Input
                    value={draft.play_min < 0 ? '' : String(draft.play_min)}
                    placeholder="不限"
                    onChange={(e) => setDraft({ ...draft, play_min: toNum(e.target.value) })}
                  />
                  <b>—</b>
                  <Input
                    value={draft.play_max < 0 ? '' : String(draft.play_max)}
                    placeholder="不限"
                    onChange={(e) => setDraft({ ...draft, play_max: toNum(e.target.value) })}
                  />
                </div>
              </label>
              <label>
                <span>账号粉丝量</span>
                <div className="range-inputs">
                  <Input
                    value={draft.follower_min < 0 ? '' : String(draft.follower_min)}
                    placeholder="不限"
                    onChange={(e) => setDraft({ ...draft, follower_min: toNum(e.target.value) })}
                  />
                  <b>—</b>
                  <Input
                    value={draft.follower_max < 0 ? '' : String(draft.follower_max)}
                    placeholder="不限"
                    onChange={(e) => setDraft({ ...draft, follower_max: toNum(e.target.value) })}
                  />
                </div>
              </label>
              {!isProject && <label>
                <span>是否已收藏</span>
                <Select
                  value={draft.collected}
                  onChange={(collected) => setDraft({ ...draft, collected })}
                  options={[
                    { value: 'all', label: '全部' },
                    { value: 'yes', label: '已加入专题' },
                    { value: 'no', label: '未加入专题' },
                  ]}
                />
              </label>}
              <label className="keyword-filter">
                <span>关键词或视频 ID</span>
                <Input
                  value={draft.query}
                  placeholder="搜索标题、账号或精确视频 ID..."
                  onChange={(e) => setDraft({ ...draft, query: e.target.value })}
                  onPressEnter={applyFilters}
                />
              </label>
              <div className="filter-actions">
                <Button onClick={resetFilters}>重置</Button>
                <Button type="primary" onClick={applyFilters}>查询</Button>
              </div>
            </div>
          </section>

          {error && (
            <Alert
              type="error"
              showIcon
              message="视频库加载失败"
              description={error}
              className="page-alert"
            />
          )}

          <Spin spinning={loading}>
            <section className="video-library-layout">
              <article className="video-list-panel card">
                <div className="video-list-head">
                  <div>
                    <strong>▶ 视频列表</strong>
                    <span>共 {data?.total || 0} 条结果</span>
                  </div>
                  <Select
                    value={filters.sort}
                    onChange={(sort) => {
                      const next = { ...filters, sort, page: 1 }
                      setFilters(next)
                      setDraft({ ...draft, sort, page: 1 })
                    }}
                    options={isProject ? [
                      { value: 'published_desc', label: '按发布时间排序' },
                      { value: 'likes_desc', label: '按点赞排序' },
                      { value: 'plays_desc', label: '按播放量排序' },
                    ] : [
                      { value: 'published_desc', label: '按发布时间排序' },
                      { value: 'priority_desc', label: '按优先级排序' },
                      { value: 'likes_desc', label: '按点赞排序' },
                      { value: 'plays_desc', label: '按播放量排序' },
                    ]}
                  />
                </div>

                <div className="video-list-table-wrap">
                  <table className="video-list-table">
                    <thead>
                      <tr>
                        {!isProject && <th className="check-col" />}
                        <th>#</th>
                        <th className="video-info-col">视频信息</th>
                        <th>账号名称</th>
                        <th>平台</th>
                        <th>来源标签</th>
                        {isProject && <th>项目状态</th>}
                        <th>发布时间</th>
                        <th>播放量</th>
                        <th>点赞</th>
                        <th>评论</th>
                        <th>分享</th>
                        <th>粉丝数</th>
                        <th><Tooltip title="点赞、评论、分享三项齐全且播放量大于零时计算；合并字段可能来自不同采集时间，不代表完播、增长或平台推荐效果。">互动/播放（合并估算）</Tooltip></th>
                        {!isProject && <th>优先级</th>}
                        {!isProject && <th>研究层级</th>}
                        {!isProject && <th>状态</th>}
                      </tr>
                    </thead>
                    <tbody>
                      {(data?.items || []).map((item, idx) => (
                        <tr
                          key={item.id}
                          className={selectedVideoId === item.id ? 'selected-detail-row' : ''}
                          onClick={() => selectDetail(item.id)}
                        >
                          {!isProject && <td onClick={(e) => e.stopPropagation()}>
                              <Checkbox
                                checked={selectedRows.has(item.id)}
                                onChange={(e) => toggleRow(item.id, e.target.checked)}
                              />
                            </td>}
                          <td>{(filters.page - 1) * filters.page_size + idx + 1}</td>
                          <td>
                            <div className="video-list-title">
                              <div className="video-list-thumb">
                                <PlatformIcon platform={item.platform} className="platform-icon-bare" />
                                <i>
                                  {item.duration_ms
                                    ? `${Math.floor(item.duration_ms / 60000)}:${String(
                                        Math.floor((item.duration_ms % 60000) / 1000),
                                      ).padStart(2, '0')}`
                                    : '—'}
                                </i>
                              </div>
                              <strong>{item.title}</strong>
                            </div>
                          </td>
                          <td>
                            <div className="account-cell">
                              <span className="avatar">{(item.account_name || '?').slice(0, 1)}</span>
                              <div>
                                <strong>{item.account_name || '未知账号'}</strong>
                                <small>{item.author_follower_count === 0
                                  ? <Tooltip title={zeroFollowerExplanation}>{formatFollowerCount(item.author_follower_count)}</Tooltip>
                                  : formatFollowerCount(item.author_follower_count)}</small>
                              </div>
                            </div>
                          </td>
                          <td><PlatformIcon platform={item.platform} className="platform-mini" /></td>
                          <td>
                            <div className="source-tags">
                              {(item.sources || []).slice(0, 2).map((s) => (
                                <Tag key={s} color="blue">{sourceName(s)}</Tag>
                              ))}
                            </div>
                          </td>
                          {isProject && <td><Tag color={item.project_inclusion_status === 'accepted'
                            ? 'green' : item.project_inclusion_status === 'rejected' ? 'default' : 'blue'}>
                            {projectInclusionLabel(item.project_inclusion_status)}
                          </Tag></td>}
                          <td>{formatDate(item.published_at)}</td>
                          <td>{hasContradictoryZeroPlay(item)
                            ? <Tooltip title="上游返回播放量 0，但已有正向互动；可能是字段缺失或合并时点不一致，不能把 0 当真实播放量。">0 · 待核</Tooltip>
                            : formatCount(item.play_count)}</td>
                          <td>{formatCount(item.like_count)}</td>
                          <td>{formatCount(item.comment_count)}</td>
                          <td>{formatCount(item.share_count)}</td>
                          <td>{item.author_follower_count === 0
                            ? <Tooltip title={zeroFollowerExplanation}>{formatFollowerCount(item.author_follower_count)}</Tooltip>
                            : formatFollowerCount(item.author_follower_count)}</td>
                          <td>{formatPlayInteractionRate(item)}</td>
                          {!isProject && <td>
                            <span className={`priority ${priorityTone(Number(item.priority || 0))}`}>
                              {priorityText(Number(item.priority || 0))}
                            </span>
                          </td>}
                          {!isProject && <td><Tag color="blue">L{item.research_level}</Tag></td>}
                          {!isProject && <td><Tag color={statusColor(item.monitoring_status)}>{statusName(item.monitoring_status)}</Tag></td>}
                        </tr>
                      ))}
                      {!data?.items?.length && (
                        <tr><td colSpan={isProject ? 14 : 16} className="empty-row">当前筛选下暂无视频</td></tr>
                      )}
                    </tbody>
                  </table>
                </div>

                <div className="video-list-footer">
                  {!isProject && <div className="bulk-actions">
                    <span>已选择 {selectedRows.size} 项</span>
                    <Button
                      type="primary"
                      disabled={!selectedRows.size}
                      loading={writeBusy}
                      onClick={() => openCollection([...selectedRows])}
                    >
                      加入专题
                    </Button>
                    <Button disabled>升入L2</Button>
                    <Button disabled>标记重点</Button>
                    <Button onClick={() => setSelectedRows(new Set())} disabled={!selectedRows.size}>
                      取消选择
                    </Button>
                  </div>}
                  <Pagination
                    current={filters.page}
                    pageSize={filters.page_size}
                    total={data?.total || 0}
                    showSizeChanger
                    pageSizeOptions={[10, 20, 50]}
                    onChange={(page, page_size) => {
                      const next = { ...filters, page, page_size }
                      setFilters(next)
                      setDraft({ ...draft, page, page_size })
                      setSelectedRows(new Set())
                    }}
                  />
                </div>
              </article>

              <aside className="video-detail-panel card">
                {!detail ? (
                  <div className="video-detail-empty">选择一条视频查看详情</div>
                ) : (
                  <>
                    <div className="detail-head">
                      <h2>▣ 视频详情</h2>
                    </div>

                    {!isProject && <VideoMediaPreview key={`video-${detail.id}`} videoId={detail.id} />}

                    <h3 className="detail-title">{detail.title}</h3>

                    {isProject && detail.project_inclusion_status !== 'accepted' && <Alert
                      type="info"
                      showIcon
                      message={detail.project_inclusion_status === 'rejected'
                        ? '本项目未采纳这条视频'
                        : detail.project_inclusion_status === 'shortlisted'
                          ? '已预选，尚未确认为项目研究依据'
                          : detail.project_inclusion_status === 'candidate'
                            ? '项目候选视频，尚未确认为研究依据'
                            : '项目依据状态待核，请刷新后再审核'}
                      description="可查看公开资料和合并指标；只有核对实际内容并明确接受的视频，才能在本项目试听私有音频或提交 ASR/L3 审核。"
                    />}

                    <div className="detail-account-row">
                      <div className="detail-account">
                        <span className="avatar large">{(detail.account_name || '?').slice(0, 1)}</span>
                        <div>
                          <strong>{detail.account_name || '未知账号'}</strong>
                          <small>{formatFollowerCount(detail.author_follower_count)} 粉丝</small>
                        </div>
                      </div>
                      {!isProject && <Button
                        type="primary"
                        loading={writeBusy}
                        onClick={() => runWrite(
                          {
                            action: 'set_monitoring',
                            asset_type: 'video',
                            asset_ids: [detail.id],
                            monitoring_status: 'follow_up',
                            monitoring_priority: Math.max(60, Number(detail.priority || 0)),
                          },
                          '该视频已加入监测。',
                        )}
                      >
                        加入监测
                      </Button>}
                    </div>

                    <div className="detail-meta">
                      <PlatformIcon platform={detail.platform} className="platform-mini" />
                      <strong>{platforms.find((p) => p.key === detail.platform)?.name || detail.platform}</strong>
                      <i />
                      <span>发布时间 {formatFullDate(detail.published_at)}</span>
                    </div>

                    <div className="detail-sources">
                      <span>来源：</span>
                      {(detail.sources || []).map((s) => (
                        <Tag key={s} color="blue">{sourceName(s)}</Tag>
                      ))}
                    </div>

                    <section className="detail-section">
                      <div className="detail-section-head">
                        <h4>数据表现</h4>
                        <span>合并数据 · 最近字段更新 {formatFullDate(detail.metric_captured_at)}</span>
                      </div>
                      {(hasContradictoryZeroPlay(detail) || detail.author_follower_count === 0) && <Alert
                        type="warning"
                        showIcon
                        message="播放量或账号粉丝的零值待核"
                        description="保留上游原值和来源供复核。播放量为 0 却有正向互动时，不能计算互动率或比较传播效果；账号粉丝为 0 时，须核对账号主页或独立账号接口后再用于筛选。"
                      />}
                      <div className="detail-metrics">
                        {[
                          [hasContradictoryZeroPlay(detail) ? '0 · 待核' : formatCount(detail.play_count), '播放量'],
                          [formatCount(detail.like_count), '点赞'],
                          [formatCount(detail.comment_count), '评论'],
                          [formatCount(detail.share_count), '分享'],
                          [formatFollowerCount(detail.author_follower_count), '账号粉丝'],
                          [formatPlayInteractionRate(detail), '互动/播放（合并估算）'],
                          ...(!isProject ? [
                            [priorityText(Number(detail.priority || 0)), '优先级'],
                            [`L${detail.research_level}`, '研究层级'],
                          ] : []),
                        ].map(([value, label]) => (
                          <div key={label}>
                            <strong>{value}</strong>
                            <span>{label}</span>
                          </div>
                        ))}
                      </div>
                    </section>

                    <details key={`metric-evidence-${detail.id}`}>
                      <summary>查看合并依据与指标历史</summary>
                      <p className="muted">播放量、账号粉丝优先采用榜单记录；其余指标采用最新非缺失记录。保留上游零值供核查；与正向互动冲突时不能当作真实播放量。各字段时间可能不同。</p>
                      {Object.entries(detail.metric_provenance || {}).map(([field, evidence]) => (
                        <p key={field} className="muted">
                          {({play_count:'播放量', like_count:'点赞', comment_count:'评论', share_count:'分享', collect_count:'收藏', author_follower_count:'账号粉丝'} as Record<string,string>)[field] || field}
                          ：{evidence.source_kind === 'billboard' ? '榜单' : evidence.source_kind === 'detail' ? '详情' : '其他'} · {formatFullDate(evidence.captured_at)}
                        </p>
                      ))}
                      <MetricTimeline videoId={detail.id} projectId={projectId} />
                    </details>

                    <RawRecordPanel videoId={detail.id} projectId={projectId} />

                    {projectId && <ProjectCaseReviewPanel
                      key={`${projectId}-${detail.id}`}
                      projectId={projectId}
                      videoId={detail.id}
                      platform={detail.platform}
                      platformVideoId={detail.platform_video_id}
                      sourceUrl={detail.source_url}
                      canWrite={projectRole === 'owner' || projectRole === 'admin' || projectRole === 'researcher'}
                      isAccepted={detail.project_inclusion_status === 'accepted'}
                    />}

                    <section className="detail-section l3-analysis-section">
                      <div className="detail-section-head">
                        <h4>{isProject ? '本项目 L3 精研结果' : 'L3 精研结果'}</h4>
                        <span>{detail.l3_analysis ? `完成于 ${formatFullDate(detail.l3_analysis.created_at)}` : '尚无已完成结果'}</span>
                      </div>
                      {!detail.l3_analysis ? (
                        <p className="muted l3-empty-state">尚无已完成的 L3 精研结果；执行中、失败或未完成记录不会在此展示。</p>
                      ) : (
                        <div className="l3-analysis-content">
                          <div className="l3-version-grid">
                            <span>模型 <b>{detail.l3_analysis.model || '未记录'}</b></span>
                            <span>修订 <b>{detail.l3_analysis.model_revision || '未记录'}</b></span>
                            <span>Prompt <b>{detail.l3_analysis.prompt_version || '未记录'}</b></span>
                            <span>Schema <b>{detail.l3_analysis.schema_version || '未记录'}</b></span>
                          </div>
                          {[
                            ['叙事结构', detail.l3_analysis.output.narrative_structure],
                            ['Hook 功能', detail.l3_analysis.output.hook_functions],
                            ['评论语义', detail.l3_analysis.output.comment_semantics],
                            ['案例比较', detail.l3_analysis.output.case_comparisons],
                            ['机制假设', detail.l3_analysis.output.mechanism_hypotheses],
                            ['IP 适配', detail.l3_analysis.output.ip_fit],
                            ['局限', detail.l3_analysis.output.limitations],
                          ].map(([label, items]) => (
                            <div className="l3-result-group" key={label}>
                              <strong>{label}</strong>
                              <ul>
                                {Array.isArray(items) && items.map((item, index) => <li key={index}>{item}</li>)}
                              </ul>
                            </div>
                          ))}
                          <div className="l3-cost-row">
                            <strong>本次 L3 任务费用</strong>
                            <span>API {formatCost(detail.l3_analysis.cost.api_cost, detail.l3_analysis.cost.currency)}</span>
                            <span>ASR {formatCost(detail.l3_analysis.cost.asr_cost, detail.l3_analysis.cost.currency)}</span>
                            <span>LLM {formatCost(detail.l3_analysis.cost.llm_cost, detail.l3_analysis.cost.currency)}</span>
                            <strong>本任务合计 {formatCost(detail.l3_analysis.cost.total_cost, detail.l3_analysis.cost.currency)}</strong>
                          </div>
                          <p className="muted">仅统计本次 L3 任务，不含此前采集、媒体处理或独立转写费用；此处 ASR 为 0 不代表上游转写免费。转写费用见下方转写结果；对应供应商日账已接入时，以其日账为准，未接入或未知不等于免费。</p>
                          <p className="l3-disclaimer">
                            {detail.l3_analysis.output.mechanism_hypotheses_are_inferences
                              ? '机制假设属于基于有限证据的模型推断。'
                              : '结果基于有限证据，仅供研究参考。'}
                            {detail.l3_analysis.cost.basis ? ` 成本口径：${detail.l3_analysis.cost.basis}。` : ''}
                          </p>
                        </div>
                      )}
                    </section>

                    {!isProject && <L3ReviewPanel
                      key={detail.id}
                      videoId={detail.id}
                      researchLevel={detail.research_level ?? 0}
                    />}

                    {!isProject && <ASRMediaReviewPanel key={`media-${detail.id}`} videoId={detail.id} />}
                    {projectId && canReviewProject && detail.project_inclusion_status === 'accepted' && <ProjectASRMediaReviewPanel
                      key={`project-media-${projectId}-${detail.id}`}
                      projectId={projectId} videoId={detail.id} />}
                    <ASRTranscriptPanel transcript={detail.asr_transcript} />
                    {projectId && detail.project_inclusion_status === 'accepted' && canReviewProject && !canReviewProjectL3 && detail.asr_transcript?.transcript_id &&
                      <p className="project-review-hint">本项目 L3 云端正文审核还需要服务端审核资质；当前账号不可提交。请由工作区管理员核对审核名单，不要借用其他项目的审核记录。</p>}
                    {projectId && detail.project_inclusion_status === 'accepted' && canReviewProjectL3 && detail.asr_transcript?.transcript_id &&
                      <ProjectL3ReviewPanel
                        key={`project-l3-${projectId}-${detail.id}-${detail.asr_transcript.transcript_id}`}
                        projectId={projectId} videoId={detail.id}
                        transcriptId={detail.asr_transcript.transcript_id} />}

                    <section className="detail-section">
                      <div className="detail-section-head">
                        <h4>来源证据</h4>
                        <span>{detail.evidence?.length || 0} 条</span>
                      </div>
                      <div className="evidence-list">
                        {(detail.evidence || []).map((e, idx) => (
                          <div key={`${e.source_type}-${idx}`}>
                            <b>{idx + 1}</b>
                            <span>{sourceName(e.source_type)}{e.source_key ? ` · ${e.source_key}` : ''}</span>
                            <time>{formatDate(e.discovered_at)}</time>
                          </div>
                        ))}
                        {!detail.evidence?.length && <p className="muted">暂无结构化来源证据</p>}
                      </div>
                    </section>

                    <section className="detail-section" aria-label="评论样本概况">
                      <div className="detail-section-head">
                        <h4>评论样本概况</h4>
                        <span>{detail.comment_features
                          ? `${detail.comment_features.feature_version} · 更新于 ${formatFullDate(detail.comment_features.calculated_at)}`
                          : '尚无 L2 评论特征'}</span>
                      </div>
                      {detail.comment_features ? <>
                        <div className="detail-metrics comment-feature-metrics">
                          {[
                            [detail.comment_features.sampled_comment_count, '去重评论样本'],
                            [detail.comment_features.source_observation_count, '评论观测记录'],
                            [detail.comment_features.eligible_text_count ?? '—', '可分析文本'],
                            [detail.comment_features.question_text_count, '含问号文本'],
                            [detail.comment_features.duplicate_text_count ?? '—', '重复文本'],
                          ].map(([value, label]) => <div key={label}><strong>{value}</strong><span>{label}</span></div>)}
                        </div>
                        <p className="muted">这是已采集评论的非随机样本，不代表全部评论或目标客群；问号、重复文本等是规则计数，不是情感或购买意向。下方仅展示最多 5 条高赞样本，不能据此推断整体偏好。</p>
                      </> : <p className="muted">尚无评论特征；下方若有评论，也不能把少量高赞样本当作整体结论。</p>}
                    </section>

                    <section className="detail-section">
                      <div className="detail-section-head">
                        <h4>热门评论样本</h4>
                        <span>{detail.comments?.length || 0} 条</span>
                      </div>
                      <div className="comment-sample-list">
                        {(detail.comments || []).map((c, idx) => (
                          <div key={c.platform_comment_id || idx}>
                            <span className="comment-avatar">{idx + 1}</span>
                            <p>{c.text_content || '（空评论）'}</p>
                            <strong>♡ {formatCount(c.like_count)}</strong>
                          </div>
                        ))}
                        {!detail.comments?.length && <p className="muted">暂无评论样本</p>}
                      </div>
                    </section>

                    {detail.source_url && (
                      <a
                        className="open-source-link"
                        href={detail.source_url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        打开原视频 ↗
                      </a>
                    )}

                    {!isProject && <div className="detail-actions">
                      <Button type="primary" loading={writeBusy} onClick={() => openCollection([detail.id])}>
                        加入专题
                      </Button>
                      <Button disabled>升入L2</Button>
                      <Button disabled>标记重点</Button>
                      <Button
                        loading={writeBusy}
                        onClick={() => runWrite(
                          {
                            action: 'add_to_collection',
                            asset_type: 'video',
                            asset_ids: [detail.id],
                            collection_name: '我的收藏',
                          },
                          '已收藏。',
                        )}
                      >
                        收藏
                      </Button>
                    </div>}
                  </>
                )}
              </aside>
            </section>
          </Spin>

          <div className="video-library-page-note">
            第 {filters.page} / {totalPages} 页 · {isProject
              ? '项目视图显示项目关联视频及其状态；只有已接受视频可进入项目私有 ASR/L3 审核，不展示全局模型审核内容'
              : '收藏、专题、监测与筛选均记录实际登录用户'}
          </div>
          {!isProject && <Modal
            title="加入专题"
            open={collectionModalOpen}
            confirmLoading={writeBusy}
            okButtonProps={{ disabled: !collectionName.trim() }}
            onOk={saveCollection}
            onCancel={() => setCollectionModalOpen(false)}
            okText="保存"
            cancelText="取消"
          >
            <Input
              aria-label="专题名称"
              value={collectionName}
              maxLength={80}
              placeholder="输入已有或新专题名称"
              onChange={(event) => setCollectionName(event.target.value)}
            />
            {!!userState?.collections.length && (
              <Select
                aria-label="选择已有专题"
                value={undefined}
                placeholder="也可以选择已有专题"
                options={userState.collections.map((item) => ({ value: item.name, label: item.name }))}
                onChange={setCollectionName}
              />
            )}
          </Modal>}
          {!isProject && <Modal
            title="保存当前筛选"
            open={filterModalOpen}
            confirmLoading={writeBusy}
            okButtonProps={{ disabled: !filterName.trim() }}
            onOk={saveCurrentFilter}
            onCancel={() => setFilterModalOpen(false)}
            okText="保存"
            cancelText="取消"
          >
            <Input
              aria-label="筛选名称"
              value={filterName}
              maxLength={80}
              placeholder="例如：近30天高优先级视频"
              onChange={(event) => setFilterName(event.target.value)}
            />
          </Modal>}
    </AppShell>
  )
}
