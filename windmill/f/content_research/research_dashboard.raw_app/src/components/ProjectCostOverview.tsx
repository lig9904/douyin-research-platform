import React, { useEffect, useState } from 'react'
import { Alert, Button, Select, Spin, Table, Tag } from 'antd'
import { backend } from '../../backend'

type CostDay = {
  cost_date: string
  cost_currency: string
  task_count: number
  asr_task_count: number
  l3_task_count: number
  known_amount: number | null
  actual_amount: number | null
  estimated_amount: number | null
  mixed_amount: number | null
  unknown_task_count: number
  unbilled_job_count: number
  cost_status: 'complete' | 'partial'
}

type CostReport = {
  project_id: string
  project_name: string
  report_timezone: string
  days: number
  records: CostDay[]
}

const amount = (value: number | null, currency: string) =>
  value === null ? '—' : `${Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 6 })} ${currency}`

export default function ProjectCostOverview({ projectId }: { projectId: string }) {
  const [days, setDays] = useState(30)
  const [report, setReport] = useState<CostReport | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = async (nextDays: number) => {
    setLoading(true)
    setError('')
    setReport(null)
    try {
      const result = await backend.get_project_daily_cost({ project_id: projectId, days: nextDays }) as CostReport
      if (result.project_id !== projectId) throw new Error('项目费用范围不匹配')
      setReport(result)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '项目费用暂不可用')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void load(days) }, [projectId])
  const unknown = report?.records.reduce((total, row) => total + row.unknown_task_count, 0) || 0

  return <div className="operations-page">
    <Alert type="info" showIcon message="项目执行账本" description="按项目、上海自然日和币种展示已记录任务；这是本地任务费用，不等于供应商账户实际日扣费。币种不折算，未知费用不计为零。" />
    <div className="operations-filters card">
      <Select value={days} onChange={(next) => { setDays(next); void load(next) }} options={[
        { value: 1, label: '今天' }, { value: 7, label: '近 7 天' },
        { value: 30, label: '近 30 天' }, { value: 90, label: '近 90 天' },
      ]} />
      <Button onClick={() => void load(days)} loading={loading}>刷新</Button>
    </div>
    {error && <Alert type="error" showIcon message="项目账本未加载" description={error} />}
    {loading ? <div className="operations-loading"><Spin /></div> : report && <>
      {unknown > 0 && <Alert type="warning" showIcon message={`有 ${unknown} 项任务金额待对账`} description="已知金额只是部分记录；不能当作本项目或供应商的实际总消费。" />}
      <section className="card operations-section">
        <h2>{report.project_name} · 每日执行费用</h2>
        <p>账期时区：{report.report_timezone}。同一日期不同币种分行显示。</p>
        <Table size="small" rowKey={(row) => `${row.cost_date}-${row.cost_currency}`} pagination={false}
          dataSource={report.records} locale={{ emptyText: '所选日期内尚无项目 ASR/L3 任务费用记录' }} columns={[
            { title: '日期', dataIndex: 'cost_date' },
            { title: '币种', dataIndex: 'cost_currency' },
            { title: '任务', render: (_, row) => `${row.task_count}（ASR ${row.asr_task_count} / L3 ${row.l3_task_count}）` },
            { title: '已知金额', render: (_, row) => amount(row.known_amount, row.cost_currency) },
            { title: '口径拆分', render: (_, row) => `实扣 ${amount(row.actual_amount, row.cost_currency)} · 估算 ${amount(row.estimated_amount, row.cost_currency)} · 混合 ${amount(row.mixed_amount, row.cost_currency)}` },
            { title: '待对账', render: (_, row) => row.unknown_task_count
              ? <><Tag color="warning">{row.unknown_task_count} 项金额未知</Tag>{row.unbilled_job_count > 0 && <Tag color="error">含 {row.unbilled_job_count} 项执行中断/未落账</Tag>}</>
              : <Tag color="green">无未知项</Tag> },
          ]} />
      </section>
    </>}
  </div>
}
