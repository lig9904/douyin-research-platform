import React, { useEffect, useState } from 'react'
import { Alert, Pagination, Select, Spin, Table, Tag } from 'antd'
import { backend } from '../../backend'
import PlatformIcon from './PlatformIcon'

type Platform = { key: string; name: string; enabled: boolean }
type Operations = {
  days: number; page: number; page_size: number; task_total: number; readonly: boolean
  api_summary: { total_calls?: number; successful_calls?: number; failed_calls?: number; cache_hits?: number }
  api_costs: { currency: string; actual_cost: number }[]
  run_summary: { total_runs?: number; running_runs?: number; failed_runs?: number }
  task_costs: { currency: string; basis: string; task_count: number; completed_count: number; failed_count: number; known_total: number }[]
  tasks: { id: string; task_type: string; task_version: string; status: string; cost_basis: string; total_cost?: number | null; cost_currency: string; created_at: string; platform?: string | null }[]
  runs: { id: string; run_type: string; run_version?: string | null; status: string; platform?: string | null; started_at: string; api_cost?: number | null; asr_cost?: number | null; llm_cost?: number | null; cost_currency?: string | null }[]
}

function value(value?: number | null) { return Number(value || 0).toLocaleString('zh-CN', { maximumFractionDigits: 6 }) }
function time(value?: string | null) { return value ? new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value)) : '—' }

export default function OperationsOverview({ platforms }: { platforms: Platform[] }) {
  const [platform, setPlatform] = useState('all')
  const [days, setDays] = useState(7)
  const [data, setData] = useState<Operations | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = async (page = 1, nextPlatform = platform, nextDays = days) => {
    setLoading(true); setError('')
    try {
      setData(await backend.get_operations_overview({ platform: nextPlatform, days: nextDays, page, page_size: 20 }) as Operations)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '运行概览暂不可用。')
    } finally { setLoading(false) }
  }
  useEffect(() => { load(1) }, [])

  return <div className="operations-page">
    <Alert type="info" showIcon message="Admin / Developer 只读概览" description="此页面不发起任务、不调用 Provider，且不展示请求指纹、原始响应、正文、错误载荷或 Secret。" />
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
          <div><h3>API 实际费用</h3>{data?.api_costs.map((cost) => <p key={cost.currency}>{cost.currency} <b>{value(cost.actual_cost)}</b></p>) || <p>暂无</p>}</div>
          <div><h3>研究任务费用</h3>{data?.task_costs.map((cost) => <p key={`${cost.currency}-${cost.basis}`}>{cost.currency} · {cost.basis}：<b>{value(cost.known_total)}</b>（{cost.task_count} 项）</p>) || <p>暂无</p>}</div>
        </div>
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
