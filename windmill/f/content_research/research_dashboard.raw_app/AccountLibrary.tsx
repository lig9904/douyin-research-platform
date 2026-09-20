import React, { useEffect, useMemo, useState } from 'react'
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
import { backend } from './wmill'
import AppShell, { type ResearchView } from './AppShell'
import PlatformIcon from './src/components/PlatformIcon'
import { mutateResearchState } from './src/components/ResearchActions'
import './account-library.css'

type Platform = {
  key: string
  name: string
  enabled: boolean
  provider_status: string
}

type AccountItem = {
  id: string
  platform: string
  platform_account_id: string
  nickname?: string | null
  profile_url?: string | null
  bio?: string | null
  location_text?: string | null
  account_type?: string | null
  certification_type?: string | null
  research_level: number
  monitoring_status: string
  monitoring_priority?: number | null
  last_seen_at?: string | null
  follower_count?: number | null
  following_count?: number | null
  total_favorited?: number | null
  video_count?: number | null
  metric_captured_at?: string | null
  posts_count: number
  avg_like_count?: number | null
  avg_comment_count?: number | null
  avg_share_count?: number | null
  play_total: number
  like_total: number
  comment_total: number
  blackhorse_count: number
  follower_growth?: number | null
  content_domains: string[]
  trend?: {
    captured_at: string
    follower_count?: number | null
    following_count?: number | null
    total_favorited?: number | null
    video_count?: number | null
  }[]
  hot_videos?: {
    id: string
    platform_video_id: string
    title: string
    source_url?: string | null
    published_at?: string | null
    duration_ms?: number | null
    play_count?: number | null
    like_count?: number | null
    comment_count?: number | null
    share_count?: number | null
    priority: number
    research_level: number
  }[]
  fan_profile_available?: boolean
  similar_accounts_available?: boolean
  similar_accounts?: {
    id: string
    nickname?: string | null
    similarity_score: number
    evidence_coverage: number
    raw_score: number
    matched_domains: string[]
    matched_fields: string[]
    components: Record<string, number>
  }[]
}

type AccountLibraryData = {
  platforms: Platform[]
  domain_options: { value: string }[]
  account_type_options: { value: string }[]
  location_options: { value: string }[]
  certification_options: { value: string }[]
  total: number
  page: number
  page_size: number
  items: AccountItem[]
  detail: AccountItem | Record<string, never>
}

type Filters = {
  platform: string
  days: number
  research_level: number
  content_domain: string
  follower_min: number
  follower_max: number
  account_type: string
  location: string
  certification_type: string
  posts_min: number
  posts_max: number
  follower_growth_min: number
  follower_growth_max: number
  monitoring_status: string
  query: string
  page: number
  page_size: number
  sort: string
}

const initialFilters: Filters = {
  platform: 'douyin',
  days: 30,
  research_level: -1,
  content_domain: 'all',
  follower_min: -1,
  follower_max: -1,
  account_type: 'all',
  location: 'all',
  certification_type: 'all',
  posts_min: -1,
  posts_max: -1,
  follower_growth_min: -1,
  follower_growth_max: -1,
  monitoring_status: 'all',
  query: '',
  page: 1,
  page_size: 10,
  sort: 'followers_desc',
}

const statusLabels: Record<string, string> = {
  untracked: '未加入',
  observe: '观察',
  monitoring: '监测中',
  selected: '重点',
  stopped: '已停止',
}

function statusColor(v?: string | null) {
  if (v === 'monitoring') return 'blue'
  if (v === 'selected') return 'green'
  if (v === 'observe') return 'cyan'
  if (v === 'stopped') return 'default'
  return 'default'
}

function formatCount(v?: number | null) {
  if (v === null || v === undefined) return '—'
  if (Math.abs(v) >= 100000000) return `${(v / 100000000).toFixed(1)}亿`
  if (Math.abs(v) >= 10000) return `${(v / 10000).toFixed(1)}万`
  return Number(v).toLocaleString('zh-CN')
}

function formatGrowth(v?: number | null) {
  if (v === null || v === undefined) return '—'
  if (v === 0) return '0'
  return `${v > 0 ? '+' : ''}${formatCount(v)}`
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

function toNum(v: string) {
  const t = v.trim()
  if (!t) return -1
  const n = Number(t)
  return Number.isFinite(n) ? n : -1
}

function FollowerTrend({
  data,
}: {
  data: NonNullable<AccountItem['trend']>
}) {
  const points = (data || []).filter((x) => x.follower_count !== null && x.follower_count !== undefined)
  if (points.length < 2) {
    return <div className="account-trend-empty">至少需要 2 个粉丝快照才能显示趋势</div>
  }

  const width = 360
  const height = 150
  const padX = 28
  const padY = 22
  const values = points.map((x) => Number(x.follower_count || 0))
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = Math.max(1, max - min)

  const coords = points.map((item, idx) => {
    const x = padX + (idx * (width - padX * 2)) / Math.max(1, points.length - 1)
    const y = height - padY - ((Number(item.follower_count || 0) - min) / range) * (height - padY * 2)
    return { x, y, item }
  })

  const polyline = coords.map((p) => `${p.x},${p.y}`).join(' ')

  return (
    <div className="account-trend-chart">
      <svg viewBox={`0 0 ${width} ${height}`}>
        {[0.25, 0.5, 0.75, 1].map((r) => (
          <line
            key={r}
            x1={padX}
            y1={height - padY - r * (height - padY * 2)}
            x2={width - padX}
            y2={height - padY - r * (height - padY * 2)}
            className="account-chart-grid"
          />
        ))}
        <polyline points={polyline} className="account-chart-line" />
        {coords.map((p, idx) => (
          <g key={idx}>
            <circle cx={p.x} cy={p.y} r="3.5" className="account-chart-dot" />
          </g>
        ))}
      </svg>
    </div>
  )
}

export default function AccountLibrary({
  onNavigate,
}: {
  onNavigate: (view: ResearchView) => void
}) {
  const [filters, setFilters] = useState<Filters>(initialFilters)
  const [draft, setDraft] = useState<Filters>(initialFilters)
  const [data, setData] = useState<AccountLibraryData | null>(null)
  const [selectedAccountId, setSelectedAccountId] = useState('')
  const [selectedRows, setSelectedRows] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [writeBusy, setWriteBusy] = useState(false)
  const [writeNotice, setWriteNotice] = useState('')
  const [writeError, setWriteError] = useState('')
  const [collectionModalOpen, setCollectionModalOpen] = useState(false)
  const [collectionName, setCollectionName] = useState('')
  const [collectionTargetIds, setCollectionTargetIds] = useState<string[]>([])
  const [detailTab, setDetailTab] = useState<'core' | 'content' | 'similar'>('core')

  const load = async (next: Filters, selected = selectedAccountId) => {
    setLoading(true)
    setError('')
    try {
      const result = (await backend.get_account_library({
        ...next,
        selected_account_id: selected,
      })) as AccountLibraryData
      setData(result)
      if (!selected && result.items.length) {
        setSelectedAccountId(result.items[0].id)
      }
      if (selected && result.detail && 'id' in result.detail) {
        setSelectedAccountId(String(result.detail.id || selected))
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load(filters, selectedAccountId)
  }, [filters])

  const detail =
    data?.detail && 'id' in data.detail ? (data.detail as AccountItem) : null

  const platforms = [
    { key: 'all', name: '全部平台', enabled: true, provider_status: 'aggregate' },
    ...(data?.platforms || []),
  ]

  const totalPages = useMemo(
    () => Math.max(1, Math.ceil((data?.total || 0) / Math.max(1, filters.page_size))),
    [data?.total, filters.page_size],
  )

  const applyFilters = () => {
    const next = { ...draft, page: 1 }
    setSelectedAccountId('')
    setSelectedRows(new Set())
    setFilters(next)
  }

  const resetFilters = () => {
    setDraft(initialFilters)
    setSelectedAccountId('')
    setSelectedRows(new Set())
    setFilters(initialFilters)
  }

  const selectPlatform = (platform: string) => {
    const next = { ...draft, platform, page: 1 }
    setDraft(next)
    setSelectedAccountId('')
    setSelectedRows(new Set())
    setFilters(next)
  }

  const selectDetail = async (id: string) => {
    setSelectedAccountId(id)
    setDetailTab('core')
    await load(filters, id)
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
      await load(filters, selectedAccountId)
    } catch (e) {
      setWriteError(e instanceof Error ? e.message : String(e))
    } finally {
      setWriteBusy(false)
    }
  }

  const monitorAccounts = (ids: string[]) => runWrite(
    {
      action: 'set_monitoring',
      asset_type: 'account',
      asset_ids: ids,
      monitoring_status: 'monitoring',
    },
    `已将 ${ids.length} 个账号加入监测。`,
  )

  const openCollection = (ids: string[]) => {
    setCollectionTargetIds(ids)
    setCollectionName('')
    setCollectionModalOpen(true)
  }

  const saveCollection = async () => {
    const name = collectionName.trim()
    if (!name || !collectionTargetIds.length) return
    await runWrite(
      {
        action: 'add_to_collection',
        asset_type: 'account',
        asset_ids: collectionTargetIds,
        collection_name: name,
      },
      `账号已加入专题「${name}」。`,
    )
    setCollectionModalOpen(false)
  }

  return (
    <AppShell
      activeView="accounts"
      onNavigate={onNavigate}
      title="账号库"
      subtitle="研究优质账号、对标内容风格、发现合作与创意机会"
      mainClassName="account-library-main"
      actions={
        <>
          <Input.Search
            className="global-search"
            placeholder="搜索账号名称、平台账号ID、简介..."
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
            ]}
          />
          <Button type="primary" disabled>导出</Button>
          <Button disabled>批量操作</Button>
        </>
      }
    >
      {writeNotice && (
        <Alert type="success" showIcon message={writeNotice} closable onClose={() => setWriteNotice('')} />
      )}
      {writeError && (
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

      <section className="account-filter-card card">
        <div className="account-filter-grid">
          <label>
            <span>账号类型</span>
            <Select
              value={draft.account_type}
              onChange={(account_type) => setDraft({ ...draft, account_type })}
              options={[
                { value: 'all', label: '全部' },
                ...(data?.account_type_options || []).map((x) => ({
                  value: x.value,
                  label: x.value,
                })),
              ]}
            />
          </label>

          <label>
            <span>内容领域</span>
            <Select
              value={draft.content_domain}
              onChange={(content_domain) => setDraft({ ...draft, content_domain })}
              options={[
                { value: 'all', label: '全部' },
                ...(data?.domain_options || []).map((x) => ({
                  value: x.value,
                  label: x.value,
                })),
              ]}
            />
          </label>

          <label>
            <span>粉丝量</span>
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

          <label>
            <span>所在地区</span>
            <Select
              value={draft.location}
              onChange={(location) => setDraft({ ...draft, location })}
              options={[
                { value: 'all', label: '全部' },
                ...(data?.location_options || []).map((x) => ({
                  value: x.value,
                  label: x.value,
                })),
              ]}
            />
          </label>

          <label>
            <span>认证类型</span>
            <Select
              value={draft.certification_type}
              onChange={(certification_type) => setDraft({ ...draft, certification_type })}
              options={[
                { value: 'all', label: '全部' },
                ...(data?.certification_options || []).map((x) => ({
                  value: x.value,
                  label: x.value,
                })),
              ]}
            />
          </label>

          <label>
            <span>近{draft.days}天发布</span>
            <div className="range-inputs">
              <Input
                value={draft.posts_min < 0 ? '' : String(draft.posts_min)}
                placeholder="不限"
                onChange={(e) => setDraft({ ...draft, posts_min: toNum(e.target.value) })}
              />
              <b>—</b>
              <Input
                value={draft.posts_max < 0 ? '' : String(draft.posts_max)}
                placeholder="不限"
                onChange={(e) => setDraft({ ...draft, posts_max: toNum(e.target.value) })}
              />
            </div>
          </label>

          <label>
            <span>近{draft.days}天增粉</span>
            <div className="range-inputs">
              <Input
                value={draft.follower_growth_min < 0 ? '' : String(draft.follower_growth_min)}
                placeholder="不限"
                onChange={(e) =>
                  setDraft({ ...draft, follower_growth_min: toNum(e.target.value) })
                }
              />
              <b>—</b>
              <Input
                value={draft.follower_growth_max < 0 ? '' : String(draft.follower_growth_max)}
                placeholder="不限"
                onChange={(e) =>
                  setDraft({ ...draft, follower_growth_max: toNum(e.target.value) })
                }
              />
            </div>
          </label>

          <label>
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
          </label>

          <label>
            <span>是否加入监测</span>
            <Select
              value={draft.monitoring_status}
              onChange={(monitoring_status) => setDraft({ ...draft, monitoring_status })}
              options={[
                { value: 'all', label: '全部' },
                { value: 'untracked', label: '未加入' },
                { value: 'observe', label: '观察' },
                { value: 'monitoring', label: '监测中' },
                { value: 'selected', label: '重点' },
              ]}
            />
          </label>

          <label className="account-keyword-filter">
            <span>关键词</span>
            <Input
              value={draft.query}
              placeholder="搜索账号名称、平台账号ID..."
              onChange={(e) => setDraft({ ...draft, query: e.target.value })}
              onPressEnter={applyFilters}
            />
          </label>

          <div className="filter-actions">
            <Button onClick={resetFilters}>重置</Button>
            <Button type="primary" onClick={applyFilters}>搜索</Button>
          </div>
        </div>
      </section>

      {error && (
        <Alert
          type="error"
          showIcon
          message="账号库加载失败"
          description={error}
          className="page-alert"
        />
      )}

      <Spin spinning={loading}>
        <section className="account-library-layout">
          <article className="account-list-panel card">
            <div className="account-list-head">
              <div>
                <strong>共 {data?.total || 0} 个账号</strong>
              </div>
              <Select
                value={filters.sort}
                onChange={(sort) => {
                  const next = { ...filters, sort, page: 1 }
                  setFilters(next)
                  setDraft({ ...draft, sort, page: 1 })
                }}
                options={[
                  { value: 'followers_desc', label: '粉丝量从高到低' },
                  { value: 'growth_desc', label: '涨粉从高到低' },
                  { value: 'posts_desc', label: '发布量从高到低' },
                  { value: 'likes_desc', label: '平均点赞从高到低' },
                  { value: 'blackhorse_desc', label: '黑马数从高到低' },
                ]}
              />
            </div>

            <div className="account-list-table-wrap">
              <table className="account-list-table">
                <thead>
                  <tr>
                    <th className="check-col" />
                    <th>#</th>
                    <th className="account-info-col">账号信息</th>
                    <th>平台</th>
                    <th>粉丝量</th>
                    <th>近{filters.days}天发布</th>
                    <th>近{filters.days}天增粉</th>
                    <th>平均点赞</th>
                    <th>内容领域</th>
                    <th>账号类型</th>
                    <th>研究层级</th>
                    <th>状态</th>
                  </tr>
                </thead>
                <tbody>
                  {(data?.items || []).map((item, idx) => (
                    <tr
                      key={item.id}
                      className={selectedAccountId === item.id ? 'selected-account-row' : ''}
                      onClick={() => selectDetail(item.id)}
                    >
                      <td onClick={(e) => e.stopPropagation()}>
                        <Checkbox
                          checked={selectedRows.has(item.id)}
                          onChange={(e) => toggleRow(item.id, e.target.checked)}
                        />
                      </td>
                      <td>{(filters.page - 1) * filters.page_size + idx + 1}</td>
                      <td>
                        <div className="account-list-info">
                          <span className="account-avatar">
                            {(item.nickname || '?').slice(0, 1)}
                          </span>
                          <div>
                            <strong>{item.nickname || '未命名账号'}</strong>
                            <small>{item.platform_account_id}</small>
                          </div>
                        </div>
                      </td>
                      <td>
                        <PlatformIcon platform={item.platform} className="platform-mini" />
                      </td>
                      <td>{formatCount(item.follower_count)}</td>
                      <td>{item.posts_count ?? 0}</td>
                      <td className={Number(item.follower_growth || 0) > 0 ? 'growth-positive' : ''}>
                        {formatGrowth(item.follower_growth)}
                      </td>
                      <td>{formatCount(item.avg_like_count)}</td>
                      <td>
                        <div className="account-domain-tags">
                          {(item.content_domains || []).slice(0, 2).map((tag) => (
                            <Tag key={tag} color="blue">{tag}</Tag>
                          ))}
                          {!item.content_domains?.length && <span>—</span>}
                        </div>
                      </td>
                      <td>{item.account_type || '—'}</td>
                      <td><Tag color="blue">L{item.research_level}</Tag></td>
                      <td>
                        <Tag color={statusColor(item.monitoring_status)}>
                          {statusLabels[item.monitoring_status] || item.monitoring_status}
                        </Tag>
                      </td>
                    </tr>
                  ))}
                  {!data?.items?.length && (
                    <tr><td colSpan={12} className="empty-row">当前筛选下暂无账号</td></tr>
                  )}
                </tbody>
              </table>
            </div>

            <div className="account-list-footer">
              <div className="bulk-actions">
                <span>已选择 {selectedRows.size} 项</span>
                <Button
                  type="primary"
                  loading={writeBusy}
                  disabled={!selectedRows.size}
                  onClick={() => monitorAccounts([...selectedRows])}
                >
                  加入监测
                </Button>
                <Button disabled>标记重点</Button>
                <Button
                  onClick={() => setSelectedRows(new Set())}
                  disabled={!selectedRows.size}
                >
                  取消选择
                </Button>
              </div>
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

          <aside className="account-detail-panel card">
            {!detail ? (
              <div className="account-detail-empty">选择一个账号查看详情</div>
            ) : (
              <>
                <div className="account-detail-head">
                  <h2>账号详情</h2>
                </div>

                <div className="account-profile">
                  <span className="account-avatar account-avatar-large">
                    {(detail.nickname || '?').slice(0, 1)}
                  </span>
                  <div className="account-profile-copy">
                    <strong>{detail.nickname || '未命名账号'}</strong>
                    <small>{detail.platform_account_id}</small>
                    <p>{detail.bio || '暂无账号简介'}</p>
                    <span>
                      {detail.location_text || '地区未知'}
                      {detail.certification_type ? ` · ${detail.certification_type}` : ''}
                    </span>
                  </div>
                  <Button type="primary" loading={writeBusy} onClick={() => monitorAccounts([detail.id])}>
                    加入监测
                  </Button>
                </div>

                <div className="account-summary-grid">
                  {[
                    [formatCount(detail.follower_count), '粉丝'],
                    [formatCount(detail.total_favorited), '获赞'],
                    [formatCount(detail.following_count), '关注'],
                    [formatCount(detail.video_count), '作品'],
                  ].map(([value, label]) => (
                    <div key={label}>
                      <strong>{value}</strong>
                      <span>{label}</span>
                    </div>
                  ))}
                </div>

                <div className="account-detail-tabs">
                  <button
                    className={detailTab === 'core' ? 'active' : ''}
                    onClick={() => setDetailTab('core')}
                  >
                    核心数据
                  </button>
                  <button
                    className={detailTab === 'content' ? 'active' : ''}
                    onClick={() => setDetailTab('content')}
                  >
                    内容分析
                  </button>
                  <button disabled title="尚未接入可靠粉丝画像数据">粉丝画像</button>
                  <button
                    className={detailTab === 'similar' ? 'active' : ''}
                    disabled={!detail.similar_accounts_available}
                    title={detail.similar_accounts_available ? '查看已存证资料的确定性相似度' : '当前资料不足以生成相似账号候选'}
                    onClick={() => setDetailTab('similar')}
                  >
                    相似账号
                  </button>
                </div>

                {detailTab === 'core' ? (
                  <>
                    <section className="account-detail-section">
                      <div className="account-detail-section-head">
                        <h4>数据趋势</h4>
                        <span>近{filters.days}天</span>
                      </div>

                      <div className="account-period-metrics">
                        {[
                          [formatGrowth(detail.follower_growth), '新增粉丝'],
                          [formatCount(detail.play_total), '播放量'],
                          [formatCount(detail.like_total), '点赞量'],
                          [formatCount(detail.comment_total), '评论量'],
                        ].map(([value, label]) => (
                          <div key={label}>
                            <strong>{value}</strong>
                            <span>{label}</span>
                          </div>
                        ))}
                      </div>

                      <FollowerTrend data={detail.trend || []} />
                    </section>

                    <section className="account-detail-section">
                      <div className="account-detail-section-head">
                        <h4>近期热门视频</h4>
                        <span>{detail.hot_videos?.length || 0} 条</span>
                      </div>
                      <div className="account-hot-videos">
                        {(detail.hot_videos || []).map((video) => (
                          <div key={video.id}>
                            <div className="account-video-thumb">
                              <PlatformIcon platform={detail.platform} className="platform-icon-bare" />
                              <i>
                                {video.duration_ms
                                  ? `${Math.floor(video.duration_ms / 60000)}:${String(
                                      Math.floor((video.duration_ms % 60000) / 1000),
                                    ).padStart(2, '0')}`
                                  : '—'}
                              </i>
                            </div>
                            <div className="account-video-copy">
                              <strong>{video.title}</strong>
                              <small>
                                {formatDate(video.published_at)} · 播放 {formatCount(video.play_count)}
                              </small>
                              <span>
                                ♡ {formatCount(video.like_count)}
                                &nbsp; · &nbsp; 评论 {formatCount(video.comment_count)}
                              </span>
                            </div>
                            <Tag color="blue">L{video.research_level}</Tag>
                          </div>
                        ))}
                        {!detail.hot_videos?.length && (
                          <p className="muted">暂无近期视频数据</p>
                        )}
                      </div>
                    </section>
                  </>
                ) : detailTab === 'content' ? (
                  <section className="account-detail-section">
                    <div className="account-detail-section-head">
                      <h4>内容分析</h4>
                      <span>仅展示确定性统计</span>
                    </div>
                    <div className="account-content-analysis">
                      <div>
                        <span>内容领域</span>
                        <div>
                          {(detail.content_domains || []).map((tag) => (
                            <Tag key={tag} color="blue">{tag}</Tag>
                          ))}
                          {!detail.content_domains?.length && <b>暂无标签</b>}
                        </div>
                      </div>
                      <div>
                        <span>近{filters.days}天发布</span>
                        <strong>{detail.posts_count || 0}</strong>
                      </div>
                      <div>
                        <span>平均点赞</span>
                        <strong>{formatCount(detail.avg_like_count)}</strong>
                      </div>
                      <div>
                        <span>平均评论</span>
                        <strong>{formatCount(detail.avg_comment_count)}</strong>
                      </div>
                      <div>
                        <span>黑马视频</span>
                        <strong>{detail.blackhorse_count || 0}</strong>
                      </div>
                      <div>
                        <span>账号类型</span>
                        <strong>{detail.account_type || '—'}</strong>
                      </div>
                    </div>
                  </section>
                ) : (
                  <section className="account-detail-section">
                    <div className="account-detail-section-head">
                      <h4>相似账号候选</h4>
                      <span>仅比较已存证资料</span>
                    </div>
                    <div className="account-similar-list">
                      {(detail.similar_accounts || []).map((account) => (
                        <div key={account.id}>
                          <div>
                            <strong>{account.nickname || '未命名账号'}</strong>
                            <span>
                              匹配领域：{account.matched_domains.join('、') || '无'}
                            </span>
                          </div>
                          <b>{account.similarity_score} 分</b>
                          <small>证据覆盖 {account.evidence_coverage}%</small>
                        </div>
                      ))}
                    </div>
                    <p className="account-similar-note">
                      分数只由内容领域、账号类型、认证类型、粉丝数和作品数的已知值计算，不表示受众或内容语义相似。
                    </p>
                  </section>
                )}

                {detail.profile_url && (
                  <a
                    href={detail.profile_url}
                    target="_blank"
                    rel="noreferrer"
                    className="account-profile-link"
                  >
                    打开原平台主页 ↗
                  </a>
                )}

                <div className="account-detail-actions">
                  <Button type="primary" loading={writeBusy} onClick={() => monitorAccounts([detail.id])}>
                    加入监测
                  </Button>
                  <Button disabled>标记重点</Button>
                  <Button loading={writeBusy} onClick={() => openCollection([detail.id])}>
                    加入专题
                  </Button>
                </div>
              </>
            )}
          </aside>
        </section>
      </Spin>

      <div className="account-library-page-note">
        第 {filters.page} / {totalPages} 页 · 监测与专题写入绑定实际登录用户
      </div>
      <Modal
        title="账号加入专题"
        open={collectionModalOpen}
        confirmLoading={writeBusy}
        okButtonProps={{ disabled: !collectionName.trim() }}
        onOk={saveCollection}
        onCancel={() => setCollectionModalOpen(false)}
        okText="保存"
        cancelText="取消"
      >
        <Input
          aria-label="账号专题名称"
          value={collectionName}
          maxLength={80}
          placeholder="输入专题名称"
          onChange={(event) => setCollectionName(event.target.value)}
        />
      </Modal>
    </AppShell>
  )
}
