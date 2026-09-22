import React, { useEffect, useState } from 'react'
import { Alert, Button, Select, Spin, Tag } from 'antd'
import AppShell, { type ResearchView } from './AppShell'
import { backend } from './backend'
import PlatformIcon from './src/components/PlatformIcon'
import './daily-briefing.css'

type BriefingItem = {
  video_id: string
  platform: string
  title: string
  account_name?: string | null
  published_at?: string | null
  last_seen_at?: string | null
  research_level: number
  monitoring_status: string
  priority: number
  play_count?: number | null
  like_count?: number | null
  comment_count?: number | null
  share_count?: number | null
  author_follower_count?: number | null
  metric_captured_at?: string | null
  metric_provenance?: Record<string, { source_kind: string; captured_at: string }>
  sources: string[]
  l3_completed: boolean
  suggested_action: string
  action_reason: string
}

type DailyBriefingData = {
  generated_at: string
  platform: string
  hours: number
  kpis: {
    observed_videos: number
    priority_candidates: number
    l3_completed: number
  }
  briefs: { active: number; due: number; next_due_at?: string | null }
  runs: { total: number; success: number; failed: number; deferred: number }
  items: BriefingItem[]
  costs: {
    supplier_daily_spend: {
      provider: string
      scope_label: string
      billing_date: string
      cost_currency: string
      total_cost?: number | null
      total_requests?: number | null
      billing_finality: string
      source_warning?: string | null
    }[]
    api_call_estimates: {
      provider: string
      currency: string
      call_count: number
      estimated_cost: number
      reconciled_cost: number
      unknown_cost_calls: number
    }[]
    research_task_costs: {
      currency: string
      cost_basis: string
      task_count: number
      known_total: number
    }[]
  }
  interpretation: string
  raw_data_drilldown: string
}

const sourceLabels: Record<string, string> = {
  low_fan: '低粉榜',
  search: '搜索',
  brief_keyword: '关键词任务',
  watched_account: '账号任务',
  detail_enrichment: '详情补全',
  creator_material: '创作榜',
}

function count(value?: number | null) {
  if (value === null || value === undefined) return '—'
  if (value >= 100000000) return `${(value / 100000000).toFixed(1)}亿`
  if (value >= 10000) return `${(value / 10000).toFixed(1)}万`
  return Number(value).toLocaleString('zh-CN')
}

function dateTime(value?: string | null) {
  if (!value) return '—'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(parsed)
}

function sourceName(value: string) {
  return sourceLabels[value] || value
}

function supplierCost(data: DailyBriefingData | null) {
  const rows = data?.costs.supplier_daily_spend || []
  if (!rows.length) return '尚未同步供应商日账'
  return rows.map((row) => (
    `${row.provider} ${row.billing_date} ${row.cost_currency} ${Number(row.total_cost || 0).toFixed(3)}`
  )).join('；')
}

function estimateCost(data: DailyBriefingData | null) {
  const rows = data?.costs.api_call_estimates || []
  if (!rows.length) return '所选时间窗内无接口费用记录'
  return rows.map((row) => (
    `${row.provider} ${row.call_count} 次 / ${row.currency} 估算 ${Number(row.estimated_cost || 0).toFixed(3)}`
  )).join('；')
}

export default function DailyBriefing({
  onNavigate,
  onOpenVideo,
}: {
  onNavigate: (view: ResearchView) => void
  onOpenVideo: (videoId: string) => void
}) {
  const [platform, setPlatform] = useState('all')
  const [hours, setHours] = useState(24)
  const [data, setData] = useState<DailyBriefingData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      setData(await backend.get_daily_briefing({ platform, hours, limit: 12 }) as DailyBriefingData)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [platform, hours])

  return (
    <AppShell
      activeView="today"
      onNavigate={onNavigate}
      title="今日研判"
      subtitle="先看合并结论，再逐条下钻原始记录"
      mainClassName="daily-briefing-main"
      actions={
        <>
          <Select
            value={platform}
            onChange={setPlatform}
            options={[{ value: 'all', label: '全部平台' }, { value: 'douyin', label: '抖音' }]}
          />
          <Select
            value={hours}
            onChange={setHours}
            options={[
              { value: 24, label: '近24小时' },
              { value: 72, label: '近3天' },
              { value: 168, label: '近7天' },
            ]}
          />
          <Button type="primary" onClick={load}>刷新</Button>
        </>
      }
    >
      {error && <Alert type="error" showIcon message="研判简报加载失败" description={error} />}
      <Alert
        className="briefing-boundary"
        type="info"
        showIcon
        message="这里展示规则排序后的事实，不把估算费用冒充实账，也不替代人工判断。"
        description={data?.raw_data_drilldown || '点击任一条目的“查看原始记录”进入视频库。'}
      />
      <Spin spinning={loading}>
        <section className="briefing-kpis">
          {[
            ['窗口内视频', data?.kpis.observed_videos || 0, `${hours} 小时内有更新`],
            ['优先候选', data?.kpis.priority_candidates || 0, '规则分 ≥ 60'],
            ['已完成 L3', data?.kpis.l3_completed || 0, '已通过隐私审核'],
            ['自动任务', data?.briefs.active || 0, `待运行 ${data?.briefs.due || 0} · 下次 ${dateTime(data?.briefs.next_due_at)}`],
          ].map(([label, value, foot]) => (
            <article className="card briefing-kpi" key={String(label)}>
              <span>{label}</span><strong>{value}</strong><small>{foot}</small>
            </article>
          ))}
        </section>

        <section className="briefing-summary-grid">
          <article className="card briefing-summary">
            <h2>运行结果</h2>
            <strong>{data?.runs.success || 0} 次成功</strong>
            <p>共 {data?.runs.total || 0} 次；失败 {data?.runs.failed || 0}；延后 {data?.runs.deferred || 0}</p>
          </article>
          <article className="card briefing-summary">
            <h2>供应商实账</h2>
            <strong>{supplierCost(data)}</strong>
            <p>以 supplier_daily_spend 为实账来源；跨时区按供应商账期显示。</p>
          </article>
          <article className="card briefing-summary">
            <h2>本窗口调用估算</h2>
            <strong>{estimateCost(data)}</strong>
            <p>这是接口记录的估算，不与供应商实账重复相加。</p>
          </article>
        </section>

        <section className="card briefing-list">
          <div className="briefing-list-head">
            <div><h2>值得先看的内容</h2><p>{data?.interpretation}</p></div>
            <span>生成于 {dateTime(data?.generated_at)}</span>
          </div>
          {!data?.items.length && <div className="briefing-empty">当前时间窗没有可研判的视频记录。</div>}
          {(data?.items || []).map((item) => (
            <article className="briefing-item" key={item.video_id}>
              <div className="briefing-item-main">
                <div className="briefing-title-line">
                  <PlatformIcon platform={item.platform} className="platform-icon-bare" />
                  <strong>{item.title}</strong>
                  <Tag color={Number(item.priority || 0) >= 80 ? 'red' : Number(item.priority || 0) >= 60 ? 'orange' : 'blue'}>
                    {Math.round(Number(item.priority || 0))} 分
                  </Tag>
                  {item.l3_completed && <Tag color="green">L3 已完成</Tag>}
                </div>
                <p>{item.account_name || '未知账号'} · 发布 {dateTime(item.published_at)} · 数据更新 {dateTime(item.metric_captured_at)}</p>
                <div className="briefing-source-tags">
                  {(item.sources || []).map((source) => <Tag key={source}>{sourceName(source)}</Tag>)}
                </div>
                <div className="briefing-action">
                  <b>{item.suggested_action}</b><span>{item.action_reason}</span>
                </div>
              </div>
              <div className="briefing-metrics">
                <span><b>{count(item.play_count)}</b>播放</span>
                <span><b>{count(item.like_count)}</b>点赞</span>
                <span><b>{count(item.comment_count)}</b>评论</span>
                <span><b>{count(item.share_count)}</b>分享</span>
              </div>
              <Button type="link" onClick={() => onOpenVideo(item.video_id)}>
                查看原始记录与完整详情 →
              </Button>
            </article>
          ))}
        </section>
      </Spin>
    </AppShell>
  )
}
