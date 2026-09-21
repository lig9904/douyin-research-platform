import React, { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Input,
  Select,
  Spin,
  Tag,
  Tooltip,
} from 'antd'
import { backend } from './backend'
import VideoLibrary from './VideoLibrary'
import AccountLibrary from './AccountLibrary'
import HotspotLibrary from './HotspotLibrary'
import AppShell, { type ResearchView } from './AppShell'
import GlobalSearch from './src/components/GlobalSearch'
import OperationsOverview from './src/components/OperationsOverview'
import PlatformIcon from './src/components/PlatformIcon'
import { supplierDailySpendFootnote } from './src/dailySpendDisplay'

type Platform = {
  key: string
  name: string
  enabled: boolean
  provider_status: string
}

type BlackhorseItem = {
  id: string
  platform: string
  platform_video_id: string
  title: string
  account_name?: string | null
  priority: number
  play_count?: number | null
  like_count?: number | null
  comment_count?: number | null
  share_count?: number | null
  author_follower_count?: number | null
  follower_efficiency?: number | null
  sources?: string[]
  source_count?: number
  research_level?: number
  monitoring_status?: string
  case_status?: string
  reason?: string
}

type Overview = {
  selected_platform: string
  hours: number
  platforms: Platform[]
  kpis: {
    new_hotspots?: number
    blackhorse_candidates?: number
    entered_l1?: number
    api_call_records?: number
  }
  api_costs: {
    currency: string
    estimated_cost: number
    reconciled_cost: number
    known_zero_calls: number
    unknown_cost_calls: number
  }[]
  supplier_daily_spend: {
    status: 'available' | 'not_synced'
    message?: string | null
    today: SupplierDailySpend[]
    records: SupplierDailySpend[]
  }
  blackhorse: BlackhorseItem[]
  trend: { day: string; value: number }[]
  keywords: { keyword: string; hits: number }[]
  cases: BlackhorseItem[]
  accounts: {
    id: string
    platform: string
    nickname?: string | null
    follower_count?: number | null
    blackhorse_count?: number
  }[]
  runtime: {
    running_jobs?: number
    success_jobs?: number
    llm_calls?: number
    api_calls?: number
    api_errors?: number
  }
  history: {
    id: string
    time: string
    run_type: string
    status: string
    input_count?: number | null
    output_count?: number | null
    promoted_l1_count?: number | null
    summary?: Record<string, unknown>
  }[]
  ip_adaptation: {
    status: string
    message: string
  }
}

type SupplierDailySpend = {
  provider: string
  account_scope: string
  bill_scope_key: string
  scope_kind: 'account_total' | 'product_subset'
  scope_label: string
  billing_date: string
  cost_currency: string
  billing_timezone: string
  total_cost?: number | null
  payable_cost?: number | null
  paid_cost?: number | null
  unpaid_cost?: number | null
  total_requests?: number | null
  paid_requests?: number | null
  billing_finality: 'preliminary' | 'final'
  source_warning?: string | null
  fetched_at?: string | null
  period_status: 'current_accumulating' | 'prior_snapshot'
  freshness_status: 'fresh' | 'stale'
}

const sourceLabels: Record<string, string> = {
  low_fan: '低粉榜',
  search: '搜索',
  creator_material: '创作榜',
  creator: '创作榜',
  watched_account: '对标账号',
  detail_enrichment: '详情',
}

function formatCount(v?: number | null) {
  if (v === null || v === undefined) return '—'
  if (v >= 100000000) return `${(v / 100000000).toFixed(1)}亿`
  if (v >= 10000) return `${(v / 10000).toFixed(1)}万`
  return v.toLocaleString('zh-CN')
}

function apiCostSummary(costs?: Overview['api_costs']) {
  if (!costs?.length) return '—'
  return costs.map((cost) => `${cost.currency} 估算 ${Number(cost.estimated_cost || 0).toFixed(3)} / 已对账 ${Number(cost.reconciled_cost || 0).toFixed(3)}`).join('；')
}

function apiCostFootnote(costs?: Overview['api_costs'], records?: number) {
  const unknown = (costs || []).reduce((sum, cost) => sum + Number(cost.unknown_cost_calls || 0), 0)
  return `${records ?? 0} 条调用记录${unknown ? ` · ${unknown} 条来源未核验` : ''}`
}

function supplierDailySpendSummary(spend?: Overview['supplier_daily_spend']) {
  if (!spend || spend.status !== 'available') return '未同步'
  if (!spend.today.length) return '当前账期暂无累计'
  return spend.today.map((item) => {
    const amount = item.total_cost
    return amount === null || amount === undefined
      ? `${item.provider} · ${item.scope_label} 待核验`
      : `${item.provider} · ${item.scope_label} ${item.cost_currency} ${Number(amount).toFixed(3)}`
  }).join('；')
}

function priorityTone(score: number) {
  if (score >= 80) return 'high'
  if (score >= 60) return 'medium'
  return 'normal'
}

function sourceName(s: string) {
  return sourceLabels[s] || s
}

function TrendChart({ data }: { data: { day: string; value: number }[] }) {
  const width = 560
  const height = 190
  const padX = 34
  const padY = 22
  const max = Math.max(1, ...data.map((x) => Number(x.value || 0)))
  const points = data.map((item, i) => {
    const x = padX + (i * (width - padX * 2)) / Math.max(1, data.length - 1)
    const y = height - padY - (Number(item.value || 0) / max) * (height - padY * 2)
    return { ...item, x, y }
  })
  const line = points.map((p) => `${p.x},${p.y}`).join(' ')

  return (
    <div className="trend-chart">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="近7天热点数量趋势">
        {[0.25, 0.5, 0.75, 1].map((r) => (
          <line
            key={r}
            x1={padX}
            y1={height - padY - r * (height - padY * 2)}
            x2={width - padX}
            y2={height - padY - r * (height - padY * 2)}
            className="chart-grid"
          />
        ))}
        {points.length > 1 && (
          <>
            <polygon
              points={`${points[0].x},${height - padY} ${line} ${points[points.length - 1].x},${height - padY}`}
              className="chart-area"
            />
            <polyline points={line} className="chart-line" />
          </>
        )}
        {points.map((p) => (
          <g key={p.day}>
            <circle cx={p.x} cy={p.y} r="4.5" className="chart-dot" />
            <text x={p.x} y={height - 4} textAnchor="middle" className="chart-label">
              {p.day}
            </text>
          </g>
        ))}
      </svg>
    </div>
  )
}

function App() {
  const [view, setView] = useState<ResearchView>('home')
  const [platform, setPlatform] = useState('douyin')
  const [hours, setHours] = useState(24)
  const [query, setQuery] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const [selectedVideoId, setSelectedVideoId] = useState('')
  const [data, setData] = useState<Overview | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = async (nextPlatform = platform, nextHours = hours) => {
    setLoading(true)
    setError('')
    try {
      const result = (await backend.get_home_overview({
        platform: nextPlatform,
        hours: nextHours,
      })) as Overview
      setData(result)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load(platform, hours)
  }, [platform, hours])

  const visibleBlackhorse = useMemo(() => {
    if (!data) return []
    const q = query.trim().toLowerCase()
    if (!q) return data.blackhorse
    return data.blackhorse.filter((item) =>
      [item.title, item.account_name, ...(item.sources || [])]
        .filter(Boolean)
        .some((x) => String(x).toLowerCase().includes(q)),
    )
  }, [data, query])

  const platforms = [
    { key: 'all', name: '全部平台', enabled: true, provider_status: 'aggregate' },
    ...(data?.platforms || []),
  ]

  if (view === 'videos') {
    return <VideoLibrary onNavigate={setView} initialSelectedVideoId={selectedVideoId} />
  }
  if (view === 'accounts') {
    return <AccountLibrary onNavigate={setView} />
  }
  if (view === 'hotspots') {
    return <HotspotLibrary onNavigate={setView} />
  }
  if (view === 'search') {
    return (
      <AppShell
        activeView="search"
        onNavigate={setView}
        title="全局搜索"
        subtitle="历史研究资产 / 公开元数据"
      >
        <GlobalSearch
          initialQuery={searchQuery}
          platforms={data?.platforms || []}
          onNavigate={(next) => setView(next)}
          onOpenVideo={(videoId) => {
            setSelectedVideoId(videoId)
            setView('videos')
          }}
        />
      </AppShell>
    )
  }
  if (view === 'cost') {
    return (
      <AppShell
        activeView="cost"
        onNavigate={(next) => setView(next)}
        title="运行与成本"
        subtitle="Admin / Developer · 只读业务运行账本"
      >
        <OperationsOverview platforms={data?.platforms || []} />
      </AppShell>
    )
  }

  return (
    <AppShell
      activeView="home"
      onNavigate={setView}
      title="内容研究台"
      subtitle="多平台内容研究平台 · 热点采集 / 对标拆解 / 趋势洞察 / IP追踪"
      actions={
        <>
          <Input.Search
            className="global-search"
            placeholder="搜索研究资产…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onSearch={(value) => {
              const normalized = value.trim()
              if (!normalized) return
              setSearchQuery(normalized)
              setView('search')
            }}
            allowClear
          />
          <Select
            value={hours}
            onChange={setHours}
            options={[
              { value: 24, label: '近24小时' },
              { value: 168, label: '近7天' },
              { value: 720, label: '近30天' },
            ]}
          />
          <Button type="primary" onClick={() => load()}>刷新数据</Button>
          <Button disabled title="日报导出将在后续页面阶段启用">导出日报</Button>
        </>
      }
    >
          <section className="platform-strip card">
            <div className="platform-title">平台分类</div>
            <div className="platform-tabs">
              {platforms.map((p) => (
                <Tooltip
                  key={p.key}
                  title={!p.enabled && p.key !== 'all' ? '已预留，数据源尚未接入' : ''}
                >
                  <button
                    className={platform === p.key ? 'platform-tab selected' : 'platform-tab'}
                    onClick={() => setPlatform(p.key)}
                  >
                    <PlatformIcon platform={p.key} className="platform-logo" />
                    {p.name}
                    {!p.enabled && <i className="planned-dot" />}
                  </button>
                </Tooltip>
              ))}
            </div>
            <div className="platform-hint">切换平台，查看对应的数据与研究资产</div>
          </section>

          {error && (
            <Alert
              className="page-alert"
              type="error"
              showIcon
              message="首页数据加载失败"
              description={error}
            />
          )}

          <Spin spinning={loading}>
            <section className="kpi-grid">
              {[
                ['🔥', '新增热点', data?.kpis.new_hotspots || 0, '过去所选时间窗'],
                ['🚀', '黑马候选', data?.kpis.blackhorse_candidates || 0, '优先级 ≥ 60'],
                ['◎', '进入 L1', data?.kpis.entered_l1 || 0, '纯代码粗筛'],
                ['◉', '供应商当日费用', supplierDailySpendSummary(data?.supplier_daily_spend), supplierDailySpendFootnote(data?.supplier_daily_spend)],
              ].map(([icon, label, value, foot]) => (
                <div className="kpi-card card" key={String(label)}>
                  <div className="kpi-icon">{icon}</div>
                  <div>
                    <span>{label}</span>
                    <strong>{value}</strong>
                    <small>{foot}</small>
                  </div>
                </div>
              ))}
            </section>

            <section className="dashboard-grid">
              <article className="panel card blackhorse-panel">
                <div className="panel-head">
                  <h2><span>▶</span> 今日黑马视频</h2>
                  <button disabled title="对应详情页将在下一阶段启用">查看更多 ›</button>
                </div>
                <div className="table-wrap">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>排名</th>
                        <th>标题</th>
                        <th>来源</th>
                        <th>点赞</th>
                        <th>评论</th>
                        <th>分享</th>
                        <th>互动/粉丝</th>
                        <th>优先级</th>
                      </tr>
                    </thead>
                    <tbody>
                      {visibleBlackhorse.length === 0 && (
                        <tr><td colSpan={8} className="empty-row">当前筛选下暂无候选</td></tr>
                      )}
                      {visibleBlackhorse.map((item, idx) => (
                        <tr key={item.id}>
                          <td><span className={`rank rank-${idx + 1}`}>{idx + 1}</span></td>
                          <td>
                            <div className="title-cell">
                              <div className="video-thumb"><PlatformIcon platform={item.platform} className="platform-icon-bare" /></div>
                              <div>
                                <strong>{item.title}</strong>
                                <small>{item.account_name || '未知账号'}</small>
                              </div>
                            </div>
                          </td>
                          <td>
                            <div className="source-tags">
                              {(item.sources || []).slice(0, 2).map((s) => (
                                <Tag key={s} color="blue">{sourceName(s)}</Tag>
                              ))}
                            </div>
                          </td>
                          <td>{formatCount(item.like_count)}</td>
                          <td>{formatCount(item.comment_count)}</td>
                          <td>{formatCount(item.share_count)}</td>
                          <td>{item.follower_efficiency == null ? '—' : `${Number(item.follower_efficiency).toFixed(1)}%`}</td>
                          <td>
                            <span className={`priority ${priorityTone(Number(item.priority || 0))}`}>
                              {Math.round(Number(item.priority || 0))}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </article>

              <article className="panel card trend-panel">
                <div className="panel-head">
                  <h2><span>▦</span> 热点趋势</h2>
                  <button disabled title="对应详情页将在下一阶段启用">查看更多 ›</button>
                </div>
                <div className="trend-layout">
                  <div>
                    <h3>近7天发现数量趋势</h3>
                    <TrendChart data={data?.trend || []} />
                  </div>
                  <div className="keyword-side">
                    <h3>热门关键词</h3>
                    <div className="keyword-cloud">
                      {(data?.keywords || []).map((x, idx) => (
                        <span key={x.keyword} className={`keyword k${idx % 6}`}>
                          {x.keyword}
                        </span>
                      ))}
                      {!data?.keywords?.length && <div className="muted">暂无结构化热点词</div>}
                    </div>
                  </div>
                </div>
              </article>

              <article className="panel card cases-panel">
                <div className="panel-head">
                  <h2><span>▣</span> 重点 Case</h2>
                  <button disabled title="对应详情页将在下一阶段启用">查看更多 ›</button>
                </div>
                <div className="case-grid">
                  {(data?.cases || []).map((item, idx) => (
                    <div className="case-card" key={item.id}>
                      <div className={`case-image case-image-${idx + 1}`}>
                        <PlatformIcon platform={item.platform} className="platform-icon-bare" />
                      </div>
                      <div className="case-body">
                        <strong>{item.title}</strong>
                        <small>{item.reason}</small>
                        <Tag
                          color={
                            item.case_status === '已入选'
                              ? 'green'
                              : item.case_status === '待跟进'
                                ? 'blue'
                                : 'orange'
                          }
                        >
                          {item.case_status}
                        </Tag>
                      </div>
                    </div>
                  ))}
                  {!data?.cases?.length && <div className="empty-card">暂无重点 Case</div>}
                </div>
              </article>

              <article className="panel card accounts-panel">
                <div className="panel-head">
                  <h2><span>◉</span> 重点对标账号</h2>
                  <button disabled title="对应详情页将在下一阶段启用">查看更多 ›</button>
                </div>
                <div className="account-table">
                  <div className="account-head"><span>账号</span><span>粉丝数</span><span>近期黑马</span><span /></div>
                  {(data?.accounts || []).map((a) => (
                    <div className="account-row" key={a.id}>
                      <div><span className="avatar">{(a.nickname || '?').slice(0, 1)}</span>{a.nickname || '未命名账号'}</div>
                      <span>{formatCount(a.follower_count)}</span>
                      <span>{a.blackhorse_count || 0}</span>
                      <button disabled>查看详情</button>
                    </div>
                  ))}
                  {!data?.accounts?.length && <div className="empty-row">暂无对标账号</div>}
                </div>
              </article>

              <article className="panel card ip-panel">
                <div className="panel-head"><h2><span>◉</span> IP 适配建议</h2></div>
                <div className="ip-pending">
                  <div className="pending-icon">◎</div>
                  <strong>等待 L2 / L3 研究证据</strong>
                  <p>{data?.ip_adaptation?.message || '当前阶段仅展示事实层，不自动生成适配建议。'}</p>
                  <Tag>不伪造结论</Tag>
                </div>
              </article>

              <article className="panel card runtime-panel">
                <div className="panel-head"><h2><span>⌁</span> 运行状态与调用</h2></div>
                <div className="status-list">
                  {[
                    ['采集任务', Number(data?.runtime.running_jobs || 0) > 0 ? '运行中' : '空闲', true],
                    ['今日成功任务', String(data?.runtime.success_jobs || 0), true],
                    ['今日 API 请求', String(data?.runtime.api_calls || 0), Number(data?.runtime.api_errors || 0) === 0],
                    ['API 错误', String(data?.runtime.api_errors || 0), Number(data?.runtime.api_errors || 0) === 0],
                    ['LLM 调用', `${data?.runtime.llm_calls || 0}（本阶段）`, Number(data?.runtime.llm_calls || 0) === 0],
                  ].map(([label, value, ok]) => (
                    <div key={String(label)} className="status-row">
                      <i className={ok ? 'status-dot ok' : 'status-dot warn'} />
                      <span>{label}</span>
                      <strong>{value}</strong>
                    </div>
                  ))}
                </div>
              </article>

              <article className="panel card history-panel">
                <div className="panel-head">
                  <h2><span>◷</span> 历史回看</h2>
                  <button disabled title="对应详情页将在下一阶段启用">查看更多 ›</button>
                </div>
                <div className="timeline">
                  {(data?.history || []).map((h) => (
                    <div className="timeline-item" key={h.id}>
                      <i />
                      <time>{h.time}</time>
                      <div>
                        <strong>{h.run_type}</strong>
                        <small>
                          {h.status} · 输入 {h.input_count ?? 0} · 输出 {h.output_count ?? 0}
                        </small>
                      </div>
                    </div>
                  ))}
                  {!data?.history?.length && <div className="muted">暂无历史任务</div>}
                </div>
              </article>
            </section>
          </Spin>
    </AppShell>
  )
}

export default App
