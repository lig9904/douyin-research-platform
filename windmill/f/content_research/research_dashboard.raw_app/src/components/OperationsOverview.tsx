import React, { useEffect, useState } from 'react'
import { Alert, Button, Modal, Pagination, Select, Spin, Table, Tag } from 'antd'
import { backend } from '../../backend'
import PlatformIcon from './PlatformIcon'

type Platform = { key: string; name: string; enabled: boolean }
type Operations = {
  days: number; page: number; page_size: number; task_total: number; readonly: boolean
  api_summary: { total_calls?: number; successful_calls?: number; failed_calls?: number; cache_hits?: number }
  api_costs: { currency: string; actual_cost: number }[]
  api_calls: { id: string; provider: string; platform: string; endpoint_key: string; status: string; http_status?: number | null; cached: boolean; estimated_cost?: number | null; actual_cost?: number | null; cost_currency: string; cost_basis: string; price_source?: string | null; pricing_version?: string | null; retry_count: number; started_at: string }[]
  run_summary: { total_runs?: number; running_runs?: number; failed_runs?: number }
  task_costs: { currency: string; basis: string; task_count: number; completed_count: number; failed_count: number; known_total: number }[]
  tasks: { id: string; task_type: string; task_version: string; status: string; cost_basis: string; total_cost?: number | null; cost_currency: string; created_at: string; platform?: string | null }[]
  runs: { id: string; run_type: string; run_version?: string | null; status: string; platform?: string | null; started_at: string; api_cost?: number | null; asr_cost?: number | null; llm_cost?: number | null; cost_currency?: string | null }[]
}

type GoldenIntakeResult = {
  status: string; source_count: number; observations: number; unique_platform_videos: number
  scored_videos: number; provider_call_count: number; uncached_call_count: number
  retry_count: number; maximum_cost_usd: number | null; raw_provider_payload_included: boolean
}

function value(value?: number | null) { return Number(value || 0).toLocaleString('zh-CN', { maximumFractionDigits: 6 }) }
function time(value?: string | null) { return value ? new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value)) : '—' }

export default function OperationsOverview({ platforms }: { platforms: Platform[] }) {
  const [platform, setPlatform] = useState('all')
  const [days, setDays] = useState(7)
  const [data, setData] = useState<Operations | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [collecting, setCollecting] = useState(false)
  const [collectionError, setCollectionError] = useState('')
  const [collectionResult, setCollectionResult] = useState<GoldenIntakeResult | null>(null)

  const load = async (page = 1, nextPlatform = platform, nextDays = days) => {
    setLoading(true); setError('')
    try {
      setData(await backend.get_operations_overview({ platform: nextPlatform, days: nextDays, page, page_size: 20 }) as Operations)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '运行概览暂不可用。')
    } finally { setLoading(false) }
  }
  useEffect(() => { load(1) }, [])

  const runGoldenIntake = () => {
    Modal.confirm({
      title: '确认执行一次真实 TikHub 采集？',
      content: '采集最多 5 条样本并补齐详情，最多 2 次外部请求、零重试；不设固定金额上限，实际费用进入账本。',
      okText: '确认执行',
      cancelText: '取消',
      async onOk() {
        setCollecting(true); setCollectionError(''); setCollectionResult(null)
        try {
          const result = await backend.run_manual_golden_intake({
            execute: true,
            confirmation: 'RUN_TIKHUB_GOLDEN_PAID',
            max_items: 5,
            max_external_calls: 2,
            max_cost_usd: null,
            date_window_hours: 24,
            enrich_details: true,
            force_refresh: true,
          }) as GoldenIntakeResult
          setCollectionResult(result)
          await load(1)
        } catch (reason) {
          setCollectionError(reason instanceof Error ? reason.message : '真实采集未完成。')
          throw reason
        } finally { setCollecting(false) }
      },
    })
  }

  return <div className="operations-page">
    <Alert type="info" showIcon message="Admin / Developer 运行台" description="账本保持只读；下方黄金采集入口必须二次确认，并由服务端限制单次请求次数、关闭自动重试。金额不设固定上限，实际调用与费用仍完整记账。页面不展示请求指纹、原始响应、正文、错误载荷或 Secret。" />
    <section className="card operations-collector">
      <div>
        <h2>测试服黄金采集</h2>
        <p>最多 5 条抖音低粉榜样本并补齐详情 · 最多 2 次 TikHub 请求 · 无固定金额上限 · 零重试</p>
      </div>
      <Button type="primary" danger loading={collecting} onClick={runGoldenIntake}>采集最多 5 条真实样本</Button>
    </section>
    {collectionError && <Alert type="error" showIcon message="真实采集未完成" description={collectionError} />}
    {collectionResult && <Alert type="success" showIcon message="真实采集完成" description={`来源 ${collectionResult.source_count} · 观测 ${collectionResult.observations} · 未缓存请求 ${collectionResult.uncached_call_count} · 重试 ${collectionResult.retry_count}`} />}
    <div className="operations-filters card">
      <Select value={platform} onChange={(next) => { setPlatform(next); load(1, next, days) }} options={[{ value: 'all', label: '全部平台' }, ...platforms.filter((item) => item.enabled).map((item) => ({ value: item.key, label: item.name }))]} />
      <Select value={days} onChange={(next) => { setDays(next); load(1, platform, next) }} options={[{ value: 1, label: '近 24 小时' }, { value: 7, label: '近 7 天' }, { value: 30, label: '近 30 天' }, { value: 90, label: '近 90 天' }]} />
    </div>
    {error && <Alert type="error" showIcon message="运行概览未加载" description={error} />}
    {loading ? <div className="operations-loading"><Spin /></div> : <>
      <div className="operations-kpis">
        <div className="card"><span>Provider 调用</span><strong>{value(data?.api_summary.total_calls)}</strong><small>成功 {value(data?.api_summary.successful_calls)} · 失败 {value(data?.api_summary.failed_calls)} · 缓存 {value(data?.api_summary.cache_hits)}</small></div>
        <div className="card"><span>业务运行</span><strong>{value(data?.run_summary.total_runs)}</strong><small>运行中 {value(data?.run_summary.running_runs)} · 失败 {value(data?.run_summary.failed_runs)}</small></div>
        <div className="card"><span>已记账任务</span><strong>{value(data?.task_total)}</strong><small>任务列表按创建时间分页</small></div>
      </div>
      <section className="card operations-section"><h2>成本口径（按币种与口径分别统计，不跨币种相加）</h2>
        <div className="operations-cost-grid">
          <div><h3>API 已记账费用</h3>{data?.api_costs.map((cost) => <p key={cost.currency}>{cost.currency} <b>{value(cost.actual_cost)}</b></p>) || <p>暂无</p>}</div>
          <div><h3>研究任务费用</h3>{data?.task_costs.map((cost) => <p key={`${cost.currency}-${cost.basis}`}>{cost.currency} · {cost.basis}：<b>{value(cost.known_total)}</b>（{cost.task_count} 项）</p>) || <p>暂无</p>}</div>
        </div>
      </section>
      <section className="card operations-section"><h2>API 调用明细</h2>
        <Table size="small" rowKey="id" pagination={false} dataSource={data?.api_calls || []} columns={[
          { title: '时间', dataIndex: 'started_at', render: time },
          { title: '接口', dataIndex: 'endpoint_key' },
          { title: '状态', dataIndex: 'status', render: (status) => <Tag color={status === 'success' ? 'green' : 'red'}>{status}</Tag> },
          { title: '缓存', dataIndex: 'cached', render: (cached) => cached ? '命中' : '外部调用' },
          { title: '费用', render: (_, row) => `${value(row.actual_cost)} ${row.cost_currency}` },
          { title: '计价依据', render: (_, row) => `${row.cost_basis}${row.pricing_version ? ` · ${row.pricing_version}` : ''}` },
          { title: '重试', dataIndex: 'retry_count' },
        ]} />
      </section>
      <section className="card operations-section"><h2>任务账本</h2>
        <Table size="small" rowKey="id" pagination={false} dataSource={data?.tasks || []} columns={[
          { title: '时间', dataIndex: 'created_at', render: time }, { title: '类型', dataIndex: 'task_type' },
          { title: '状态', dataIndex: 'status', render: (status) => <Tag color={status === 'completed' ? 'green' : status === 'failed' ? 'red' : 'default'}>{status}</Tag> },
          { title: '费用', render: (_, row) => `${value(row.total_cost)} ${row.cost_currency}` }, { title: '口径', dataIndex: 'cost_basis' },
        ]} />
        {(data?.task_total || 0) > (data?.page_size || 20) && <Pagination current={data?.page || 1} pageSize={data?.page_size || 20} total={data?.task_total || 0} showSizeChanger={false} onChange={(page) => load(page)} />}
      </section>
      <section className="card operations-section"><h2>最近业务运行</h2>
        <Table size="small" rowKey="id" pagination={false} dataSource={data?.runs || []} columns={[
          { title: '开始时间', dataIndex: 'started_at', render: time }, { title: '运行类型', dataIndex: 'run_type' },
          { title: '状态', dataIndex: 'status', render: (status) => <Tag>{status}</Tag> },
          { title: '平台', dataIndex: 'platform', render: (platform) => platform ? <span className="operations-platform"><PlatformIcon platform={platform} className="platform-mini" />{platform}</span> : '—' },
        ]} />
      </section>
    </>}
  </div>
}
