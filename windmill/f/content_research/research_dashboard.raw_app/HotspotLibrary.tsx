import React, { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Checkbox,
  Input,
  Pagination,
  Select,
  Spin,
  Tag,
  Tooltip,
} from 'antd'
import { backend } from './wmill'
import AppShell, { type ResearchView } from './AppShell'
import './hotspot-library.css'

type Platform = {
  key: string
  name: string
  enabled: boolean
  provider_status: string
}

type RelatedVideo = {
  id: string
  platform: string
  platform_video_id: string
  title: string
  source_url?: string | null
  published_at?: string | null
  duration_ms?: number | null
  research_level: number
  account_name?: string | null
  play_count?: number | null
  like_count?: number | null
  comment_count?: number | null
  share_count?: number | null
  priority?: number | null
}

type HotspotItem = {
  id: string
  provider: string
  platform: string
  signal_type: string
  provider_signal_id?: string | null
  signal_key: string
  title?: string | null
  description?: string | null
  category_key?: string | null
  city_code?: string | null
  research_level: number
  monitoring_status: string
  monitoring_priority?: number | null
  first_seen_at?: string | null
  last_seen_at?: string | null
  snapshot_captured_at?: string | null
  rank_value?: number | null
  rank_change?: number | null
  heat_value?: number | null
  discussion_count?: number | null
  related_video_count: number
  collection_count: number
  heat_growth_pct?: number | null
  trend?: {
    captured_at: string
    rank_value?: number | null
    rank_change?: number | null
    heat_value?: number | null
    value_json?: Record<string, unknown> | null
  }[]
  related_videos?: RelatedVideo[]
}

type HotspotLibraryData = {
  platforms: Platform[]
  signal_type_options: { value: string }[]
  category_options: { value: string }[]
  total: number
  page: number
  page_size: number
  items: HotspotItem[]
  detail: HotspotItem | Record<string, never>
}

type Filters = {
  platform: string
  days: number
  signal_type: string
  category: string
  research_level: number
  monitoring_status: string
  heat_min: number
  query: string
  page: number
  page_size: number
  sort: string
}

const initialFilters: Filters = {
  platform: 'douyin',
  days: 7,
  signal_type: 'all',
  category: 'all',
  research_level: -1,
  monitoring_status: 'all',
  heat_min: -1,
  query: '',
  page: 1,
  page_size: 10,
  sort: 'heat_desc',
}

const platformGlyph: Record<string, string> = {
  all: '▦',
  douyin: '♪',
  kuaishou: '∞',
  wechat_channels: '◉',
  xiaohongshu: '小',
  bilibili: 'B',
  weibo: '◎',
}

const signalTypeLabels: Record<string, string> = {
  hot_search: '热搜',
  rising_search: '飙升搜索',
  rising_topic: '飙升话题',
  hot_topic: '热门话题',
  city_hot: '同城热点',
  creator_topic: '创作者话题',
  creator_material: '热门素材',
  creator_music: '热门音乐',
  rising_hashtag: '上升标签',
}

const statusLabels: Record<string, string> = {
  untracked: '未加入',
  observe: '观察',
  monitoring: '监测中',
  selected: '重点',
  stopped: '已停止',
}

function signalTypeName(v: string) {
  return signalTypeLabels[v] || v
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
  const n = Number(v)
  if (Math.abs(n) >= 100000000) return `${(n / 100000000).toFixed(1)}亿`
  if (Math.abs(n) >= 10000) return `${(n / 10000).toFixed(1)}万`
  return n.toLocaleString('zh-CN')
}

function formatGrowth(v?: number | null) {
  if (v === null || v === undefined) return '—'
  const n = Number(v)
  return `${n > 0 ? '+' : ''}${n.toFixed(0)}%`
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

function HotspotTrend({
  data,
}: {
  data: NonNullable<HotspotItem['trend']>
}) {
  const points = (data || []).filter((x) => x.heat_value !== null && x.heat_value !== undefined)
  if (points.length < 2) {
    return <div className="hotspot-trend-empty">至少需要 2 个热度快照才能显示趋势</div>
  }

  const width = 360
  const height = 160
  const padX = 28
  const padY = 22
  const values = points.map((x) => Number(x.heat_value || 0))
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = Math.max(1, max - min)

  const coords = points.map((item, idx) => {
    const x = padX + (idx * (width - padX * 2)) / Math.max(1, points.length - 1)
    const y = height - padY - ((Number(item.heat_value || 0) - min) / range) * (height - padY * 2)
    return { x, y, item }
  })
  const line = coords.map((p) => `${p.x},${p.y}`).join(' ')

  return (
    <div className="hotspot-trend-chart">
      <svg viewBox={`0 0 ${width} ${height}`}>
        {[0.25, 0.5, 0.75, 1].map((r) => (
          <line
            key={r}
            x1={padX}
            y1={height - padY - r * (height - padY * 2)}
            x2={width - padX}
            y2={height - padY - r * (height - padY * 2)}
            className="hotspot-chart-grid"
          />
        ))}
        <polygon
          points={`${coords[0].x},${height - padY} ${line} ${coords[coords.length - 1].x},${height - padY}`}
          className="hotspot-chart-area"
        />
        <polyline points={line} className="hotspot-chart-line" />
        {coords.map((p, idx) => (
          <circle key={idx} cx={p.x} cy={p.y} r="3.5" className="hotspot-chart-dot" />
        ))}
      </svg>
    </div>
  )
}

export default function HotspotLibrary({
  onNavigate,
}: {
  onNavigate: (view: ResearchView) => void
}) {
  const [filters, setFilters] = useState<Filters>(initialFilters)
  const [draft, setDraft] = useState<Filters>(initialFilters)
  const [data, setData] = useState<HotspotLibraryData | null>(null)
  const [selectedSignalId, setSelectedSignalId] = useState('')
  const [selectedRows, setSelectedRows] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = async (next: Filters, selected = selectedSignalId) => {
    setLoading(true)
    setError('')
    try {
      const result = (await backend.get_hotspot_library({
        ...next,
        selected_signal_id: selected,
      })) as HotspotLibraryData
      setData(result)
      if (!selected && result.items.length) {
        setSelectedSignalId(result.items[0].id)
      }
      if (selected && result.detail && 'id' in result.detail) {
        setSelectedSignalId(String(result.detail.id || selected))
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load(filters, selectedSignalId)
  }, [filters])

  const detail =
    data?.detail && 'id' in data.detail ? (data.detail as HotspotItem) : null

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
    setSelectedSignalId('')
    setSelectedRows(new Set())
    setFilters(next)
  }

  const resetFilters = () => {
    setDraft(initialFilters)
    setSelectedSignalId('')
    setSelectedRows(new Set())
    setFilters(initialFilters)
  }

  const selectPlatform = (platform: string) => {
    const next = { ...draft, platform, page: 1 }
    setDraft(next)
    setSelectedSignalId('')
    setSelectedRows(new Set())
    setFilters(next)
  }

  const selectDetail = async (id: string) => {
    setSelectedSignalId(id)
    await load(filters, id)
  }

  const toggleRow = (id: string, checked: boolean) => {
    const next = new Set(selectedRows)
    if (checked) next.add(id)
    else next.delete(id)
    setSelectedRows(next)
  }

  return (
    <AppShell
      activeView="hotspots"
      onNavigate={onNavigate}
      title="热点库"
      subtitle="追踪全网热点、捕捉内容趋势、发现创作机会"
      mainClassName="hotspot-library-main"
      actions={
        <>
          <Input.Search
            className="global-search"
            placeholder="搜索热点词、话题、事件、关键词..."
            value={draft.query}
            onChange={(e) => setDraft({ ...draft, query: e.target.value })}
            onSearch={applyFilters}
            allowClear
          />
          <Select
            value={draft.days}
            onChange={(days) => setDraft({ ...draft, days })}
            options={[
              { value: 1, label: '近24小时' },
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
                <span className={`platform-logo ${p.key}`}>
                  {platformGlyph[p.key] || '•'}
                </span>
                {p.name}
                {!p.enabled && <i className="planned-dot" />}
              </button>
            </Tooltip>
          ))}
        </div>
      </section>

      <section className="hotspot-filter-card card">
        <div className="hotspot-filter-grid">
          <label>
            <span>时间范围</span>
            <Select
              value={draft.days}
              onChange={(days) => setDraft({ ...draft, days })}
              options={[
                { value: 1, label: '近24小时' },
                { value: 7, label: '近7天' },
                { value: 30, label: '近30天' },
                { value: 90, label: '近90天' },
              ]}
            />
          </label>

          <label>
            <span>热点类型</span>
            <Select
              value={draft.signal_type}
              onChange={(signal_type) => setDraft({ ...draft, signal_type })}
              options={[
                { value: 'all', label: '全部' },
                ...(data?.signal_type_options || []).map((x) => ({
                  value: x.value,
                  label: signalTypeName(x.value),
                })),
              ]}
            />
          </label>

          <label>
            <span>内容领域</span>
            <Select
              value={draft.category}
              onChange={(category) => setDraft({ ...draft, category })}
              options={[
                { value: 'all', label: '全部' },
                ...(data?.category_options || []).map((x) => ({
                  value: x.value,
                  label: x.value,
                })),
              ]}
            />
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
            <span>状态</span>
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

          <label>
            <span>最低热度</span>
            <Input
              value={draft.heat_min < 0 ? '' : String(draft.heat_min)}
              placeholder="不限"
              onChange={(e) => setDraft({ ...draft, heat_min: toNum(e.target.value) })}
            />
          </label>

          <label className="hotspot-keyword-filter">
            <span>关键词</span>
            <Input
              value={draft.query}
              placeholder="搜索热点词、话题、事件..."
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
          message="热点库加载失败"
          description={error}
          className="page-alert"
        />
      )}

      <Spin spinning={loading}>
        <section className="hotspot-library-layout">
          <article className="hotspot-list-panel card">
            <div className="hotspot-list-head">
              <div>
                <strong>共 {data?.total || 0} 个热点</strong>
              </div>
              <Select
                value={filters.sort}
                onChange={(sort) => {
                  const next = { ...filters, sort, page: 1 }
                  setFilters(next)
                  setDraft({ ...draft, sort, page: 1 })
                }}
                options={[
                  { value: 'heat_desc', label: '热度从高到低' },
                  { value: 'growth_desc', label: '涨幅从高到低' },
                  { value: 'discussion_desc', label: '讨论量从高到低' },
                  { value: 'related_desc', label: '相关视频从多到少' },
                  { value: 'rank_asc', label: '榜单排名' },
                  { value: 'recent_desc', label: '最近更新' },
                ]}
              />
            </div>

            <div className="hotspot-list-table-wrap">
              <table className="hotspot-list-table">
                <thead>
                  <tr>
                    <th className="check-col" />
                    <th>#</th>
                    <th className="hotspot-info-col">热点词 / 话题</th>
                    <th>平台</th>
                    <th>热点类型</th>
                    <th>热度指数</th>
                    <th>讨论量</th>
                    <th>相关视频</th>
                    <th>上升趋势</th>
                    <th>主要领域</th>
                    <th>研究层级</th>
                    <th>状态</th>
                  </tr>
                </thead>
                <tbody>
                  {(data?.items || []).map((item, idx) => (
                    <tr
                      key={item.id}
                      className={selectedSignalId === item.id ? 'selected-hotspot-row' : ''}
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
                        <div className="hotspot-list-info">
                          <span className="hotspot-thumb">{platformGlyph[item.platform] || '◆'}</span>
                          <div>
                            <strong>{item.title || item.signal_key}</strong>
                            <small>{signalTypeName(item.signal_type)} · {item.signal_key}</small>
                          </div>
                        </div>
                      </td>
                      <td>
                        <span className="platform-mini">
                          {platformGlyph[item.platform] || '•'}
                        </span>
                      </td>
                      <td><Tag color="blue">{signalTypeName(item.signal_type)}</Tag></td>
                      <td className="heat-value">{formatCount(item.heat_value)}</td>
                      <td>{formatCount(item.discussion_count)}</td>
                      <td>{item.related_video_count || 0}</td>
                      <td className={Number(item.heat_growth_pct || 0) > 0 ? 'growth-positive' : ''}>
                        {formatGrowth(item.heat_growth_pct)}
                      </td>
                      <td>{item.category_key ? <Tag color="purple">{item.category_key}</Tag> : '—'}</td>
                      <td><Tag color="blue">L{item.research_level}</Tag></td>
                      <td>
                        <Tag color={statusColor(item.monitoring_status)}>
                          {statusLabels[item.monitoring_status] || item.monitoring_status}
                        </Tag>
                      </td>
                    </tr>
                  ))}
                  {!data?.items?.length && (
                    <tr><td colSpan={12} className="empty-row">当前筛选下暂无热点</td></tr>
                  )}
                </tbody>
              </table>
            </div>

            <div className="hotspot-list-footer">
              <div className="bulk-actions">
                <span>已选择 {selectedRows.size} 项</span>
                <Button type="primary" disabled>加入专题</Button>
                <Button disabled>加入监测</Button>
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

          <aside className="hotspot-detail-panel card">
            {!detail ? (
              <div className="hotspot-detail-empty">选择一个热点查看详情</div>
            ) : (
              <>
                <div className="hotspot-detail-head">
                  <h2>热点详情</h2>
                </div>

                <div className="hotspot-profile">
                  <span className="hotspot-detail-thumb">
                    {platformGlyph[detail.platform] || '◆'}
                  </span>
                  <div>
                    <strong>{detail.title || detail.signal_key}</strong>
                    <small>{signalTypeName(detail.signal_type)} · {detail.signal_key}</small>
                    <p>{detail.description || '暂无结构化热点说明'}</p>
                  </div>
                  <Button type="primary" disabled>加入监测</Button>
                </div>

                <div className="hotspot-meta-row">
                  <span className="platform-mini">{platformGlyph[detail.platform] || '•'}</span>
                  <strong>{platforms.find((p) => p.key === detail.platform)?.name || detail.platform}</strong>
                  <i />
                  <span>首次发现 {formatDate(detail.first_seen_at)}</span>
                </div>

                <div className="hotspot-summary-grid">
                  {[
                    [formatCount(detail.heat_value), '热度指数'],
                    [formatCount(detail.discussion_count), '讨论量'],
                    [String(detail.related_video_count || 0), '相关视频'],
                    [formatGrowth(detail.heat_growth_pct), '相邻快照涨幅'],
                  ].map(([value, label]) => (
                    <div key={label}>
                      <strong>{value}</strong>
                      <span>{label}</span>
                    </div>
                  ))}
                </div>

                <div className="hotspot-tags">
                  {detail.category_key && <Tag color="blue">{detail.category_key}</Tag>}
                  {detail.city_code && <Tag>{detail.city_code}</Tag>}
                  <Tag color="purple">{signalTypeName(detail.signal_type)}</Tag>
                  <Tag color="blue">L{detail.research_level}</Tag>
                </div>

                <section className="hotspot-detail-section">
                  <div className="hotspot-detail-section-head">
                    <h4>热度趋势</h4>
                    <span>近{filters.days}天</span>
                  </div>
                  <HotspotTrend data={detail.trend || []} />
                </section>

                <section className="hotspot-detail-section">
                  <div className="hotspot-detail-section-head">
                    <h4>相关热门视频</h4>
                    <span>{detail.related_videos?.length || 0} 条</span>
                  </div>
                  <div className="hotspot-related-videos">
                    {(detail.related_videos || []).map((video, idx) => (
                      <div key={video.id}>
                        <b>{idx + 1}</b>
                        <span className="hotspot-video-thumb">
                          {platformGlyph[video.platform] || '▶'}
                        </span>
                        <div>
                          <strong>{video.title}</strong>
                          <small>
                            {video.account_name || '未知账号'} · {formatDate(video.published_at)}
                          </small>
                          <span>
                            播放 {formatCount(video.play_count)} · 点赞 {formatCount(video.like_count)}
                          </span>
                        </div>
                        <Tag color="blue">L{video.research_level}</Tag>
                      </div>
                    ))}
                    {!detail.related_videos?.length && (
                      <p className="muted">暂无已建立关联的视频</p>
                    )}
                  </div>
                </section>

                <div className="hotspot-detail-actions">
                  <Button type="primary" disabled>加入专题</Button>
                  <Button disabled>加入监测</Button>
                  <Button disabled>标记重点</Button>
                </div>
              </>
            )}
          </aside>
        </section>
      </Spin>

      <div className="hotspot-library-page-note">
        第 {filters.page} / {totalPages} 页 · 涨幅仅在存在至少2个热度快照时显示
      </div>
    </AppShell>
  )
}
