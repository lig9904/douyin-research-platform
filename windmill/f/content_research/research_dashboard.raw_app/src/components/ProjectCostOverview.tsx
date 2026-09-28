import React, { useEffect, useRef, useState } from 'react'
import { Alert, Button, Select, Spin, Table, Tag } from 'antd'
import { backend } from '../../backend'

type CostDay = {
  cost_date: string
  cost_currency: string
  task_count: number
  asr_task_count: number
  l3_task_count: number
  las_task_count: number
  discovery_run_count: number
  comment_run_count: number
  profile_run_count: number
  statistics_run_count: number
  known_amount: number | null
  actual_amount: number | null
  estimated_amount: number | null
  mixed_amount: number | null
  unknown_task_count: number
  unbilled_job_count: number
  las_pending_bill_count: number
  cost_status: 'complete' | 'partial'
}

type CostReport = {
  project_id: string
  project_name: string
  report_timezone: string
  days: number
  records: CostDay[]
  las_receipts: LasReceipt[]
  las_receipts_truncated: boolean
  las_open_attempts: LasOpenAttempt[]
  las_open_attempts_truncated: boolean
  las_supplier_daily: LasSupplierDay[]
}

type LasSupplierDay = {
  cost_date: string
  billing_timezone: string
  provider: string
  account_scope: string
  cost_currency: string
  supplier_line_count: number
  las_task_count: number
  registered_line_amount: number
}

type LasOpenAttempt = {
  id: string
  platform_video_id: string
  title: string | null
  task_key: string
  attempt_no: number
  status: string
  provider_task_ref: string | null
  submission_count: number
  error_code: string | null
  cost_currency: string
  created_at: string
}

type LasReceipt = {
  id: string
  platform_video_id: string
  title: string | null
  provider_task_ref: string
  status: string
  model_id: string
  token_usages: Record<string, unknown>
  estimated_cost: number | null
  cost_currency: string
  pricing_version: string | null
  executed_at: string
  supplier_line_count: number
  registered_supplier_amount: number | null
  supplier_billing_dates: string[] | null
  supplier_reconciliation_status: 'partial' | 'final' | null
}

const amount = (value: number | null, currency: string) =>
  value === null ? '—' : `${Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 6 })} ${currency}`
const shanghaiTime = (value: string) => new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai', dateStyle: 'short', timeStyle: 'medium',
}).format(new Date(value))

export default function ProjectCostOverview({ projectId }: { projectId: string }) {
  const [days, setDays] = useState(30)
  const [loadedReport, setLoadedReport] = useState<CostReport | null>(null)
  const [loading, setLoading] = useState(true)
  const [errorState, setErrorState] = useState<{ projectId: string, message: string } | null>(null)
  const latestRequest = useRef(0)
  const currentProjectId = useRef(projectId)
  currentProjectId.current = projectId
  const report = loadedReport?.project_id === projectId ? loadedReport : null
  const error = errorState?.projectId === projectId ? errorState.message : ''

  const load = async (nextDays: number) => {
    const requestedProjectId = projectId
    const requestNo = ++latestRequest.current
    setLoading(true)
    setErrorState(null)
    setLoadedReport(null)
    try {
      const result = await backend.get_project_daily_cost({ project_id: requestedProjectId, days: nextDays }) as CostReport
      if (requestNo !== latestRequest.current || currentProjectId.current !== requestedProjectId) return
      if (result.project_id !== requestedProjectId) throw new Error('项目费用范围不匹配')
      setLoadedReport(result)
    } catch (reason) {
      if (requestNo === latestRequest.current && currentProjectId.current === requestedProjectId) {
        setErrorState({ projectId: requestedProjectId, message: reason instanceof Error ? reason.message : '项目费用暂不可用' })
      }
    } finally {
      if (requestNo === latestRequest.current && currentProjectId.current === requestedProjectId) setLoading(false)
    }
  }

  useEffect(() => { void load(days) }, [projectId])
  const unknown = report?.records.reduce((total, row) => total + row.unknown_task_count, 0) || 0

  return <div className="operations-page">
    <Alert type="info" showIcon message="项目执行账本" description="按项目、上海自然日和币种展示发现、评论、账号核验、ASR、L3 与 LAS 整片任务。这里的日期是任务执行日，不一定是供应商账单日；LAS 金额只有估价，取得并核对供应商分笔账前不能称为实扣。其他任务的“记录为实际”沿用原任务口径，并非经供应商分笔对账。币种不折算，未知费用不计为零。" />
    <div className="operations-filters card">
      <Select value={days} onChange={(next) => { setDays(next); void load(next) }} options={[
        { value: 1, label: '今天' }, { value: 7, label: '近 7 天' },
        { value: 30, label: '近 30 天' }, { value: 90, label: '近 90 天' },
      ]} />
      <Button onClick={() => void load(days)} loading={loading}>刷新</Button>
    </div>
    {error && <Alert type="error" showIcon message="项目账本未加载" description={error} />}
    {loading ? <div className="operations-loading"><Spin /></div> : report && <>
      {unknown > 0 && <Alert type="warning" showIcon message={`有 ${unknown} 项任务金额或执行状态待对账`} description="已知金额可能只是失败任务的部分报价；不能当作本项目或供应商的实际总消费。" />}
      <section className="card operations-section">
        <h2>{report.project_name} · 每日执行费用</h2>
        <p>账期时区：{report.report_timezone}。同一日期不同币种分行显示。</p>
        <Table size="small" rowKey={(row) => `${row.cost_date}-${row.cost_currency}`} pagination={false}
          dataSource={report.records} locale={{ emptyText: '所选日期内尚无项目任务费用记录' }} columns={[
            { title: '日期', dataIndex: 'cost_date' },
            { title: '币种', dataIndex: 'cost_currency' },
            { title: '任务', render: (_, row) => `${row.task_count}（发现 ${row.discovery_run_count} / 评论 ${row.comment_run_count} / 账号核验 ${row.profile_run_count} / 播放核验 ${row.statistics_run_count ?? 0} / ASR ${row.asr_task_count} / L3 ${row.l3_task_count} / LAS ${row.las_task_count ?? 0}）` },
            { title: '已知金额', render: (_, row) => amount(row.known_amount, row.cost_currency) },
            { title: '口径拆分', render: (_, row) => `任务记录为实际 ${amount(row.actual_amount, row.cost_currency)} · 任务估算 ${amount(row.estimated_amount, row.cost_currency)} · 混合 ${amount(row.mixed_amount, row.cost_currency)}` },
            { title: '待对账', render: (_, row) => row.unknown_task_count
              ? <><Tag color="warning">{row.unknown_task_count} 项金额或执行状态未闭合</Tag>{row.unbilled_job_count > 0 && <Tag color="error">含 {row.unbilled_job_count} 项执行中断/未落账</Tag>}{row.las_pending_bill_count > 0 && <Tag color="blue">LAS {row.las_pending_bill_count} 项待供应商分笔账</Tag>}</>
              : <Tag color="green">无未知项</Tag> },
          ]} />
      </section>
      <section className="card operations-section">
        <h2>LAS 供应商账单日 · 已登记分笔</h2>
        <p>仅列已登记的供应商分笔，尚需与真实导出或 API 独立核验来源；按供应商、账户和账单时区拆分。不与任务执行日估价相加，也不代表火山账户当日全部实扣。</p>
        <Table size="small" rowKey={(row) => `${row.cost_date}-${row.billing_timezone}-${row.provider}-${row.account_scope}-${row.cost_currency}`} pagination={false}
          dataSource={report.las_supplier_daily || []} locale={{ emptyText: '暂无已登记的 LAS 供应商分笔；不能据此认定费用为零。' }} columns={[
            { title: '供应商账单日', dataIndex: 'cost_date' },
            { title: '时区 / 供应商 / 账户', render: (_, row) => `${row.billing_timezone} / ${row.provider} / ${row.account_scope}` },
            { title: '币种', dataIndex: 'cost_currency' },
            { title: '分笔 / 任务', render: (_, row) => `${row.supplier_line_count} / ${row.las_task_count}` },
            { title: '已登记金额（来源待核）', render: (_, row) => amount(row.registered_line_amount, row.cost_currency) },
          ]} />
      </section>
      <section className="card operations-section">
        <h2>LAS 整片分析 · 逐任务凭证</h2>
        <p>模型结论不是人工核片；这里仅列任务与费用依据。任务估价按执行日记录，供应商分笔金额按其账单日核对，两者不相加。</p>
        {report.las_receipts_truncated && <Alert type="warning" showIcon message="仅显示最新 500 项 LAS 任务，请缩短日期范围后复查。" />}
        <Table size="small" rowKey="id" pagination={{ pageSize: 10 }}
          dataSource={report.las_receipts || []} locale={{ emptyText: '所选日期内暂无已登记的 LAS 任务；这不代表供应商未发生费用。' }} columns={[
            { title: '执行时间（上海）', render: (_, row) => shanghaiTime(row.executed_at) },
            { title: '视频', render: (_, row) => <span>{row.title || row.platform_video_id}<br /><small>{row.platform_video_id}</small></span> },
            { title: '任务', render: (_, row) => <span>{row.status}<br /><small>{row.provider_task_ref}</small></span> },
            { title: '模型与用量', render: (_, row) => <span>{row.model_id}<br /><small>{JSON.stringify(row.token_usages)}</small></span> },
            { title: '任务估价', render: (_, row) => <span>{amount(row.estimated_cost, row.cost_currency)}<br /><small>{row.pricing_version || '无价格版本'}</small></span> },
            { title: '供应商分笔', render: (_, row) => row.supplier_line_count > 0
              ? <span>{amount(row.registered_supplier_amount, row.cost_currency)}<br /><small>账单日 {(row.supplier_billing_dates || []).join('、')}</small><br /><Tag color="warning">分笔已登记，来源待核</Tag></span>
              : <Tag color="warning">待对账，非零费用证明</Tag> },
          ]} />
        {(report.las_open_attempts?.length || 0) > 0 && <>
          <h3>待完成或待回查的调用尝试</h3>
          <p>请求已经发出但未取得可验证的终态时，费用保持未知；不会自动重试付费调用。</p>
          {report.las_open_attempts_truncated && <Alert type="warning" showIcon message="仅显示最新 500 项未闭合尝试。" />}
          <Table size="small" rowKey="id" pagination={{ pageSize: 10 }} dataSource={report.las_open_attempts} columns={[
            { title: '建档时间（上海）', render: (_, row) => shanghaiTime(row.created_at) },
            { title: '视频', render: (_, row) => row.title || row.platform_video_id },
            { title: '任务', render: (_, row) => `${row.task_key} · 第 ${row.attempt_no} 次` },
            { title: '状态', render: (_, row) => <><Tag color="warning">{row.status}</Tag>{row.submission_count > 0 ? '已声明提交' : '尚未声明提交'}</> },
            { title: '供应商任务号', render: (_, row) => row.provider_task_ref || '未知，需回查' },
            { title: '费用', render: (_, row) => `未知（${row.cost_currency}）` },
          ]} />
        </>}
      </section>
    </>}
  </div>
}
