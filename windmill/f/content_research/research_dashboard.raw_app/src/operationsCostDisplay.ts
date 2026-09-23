type TaskCostSummary = {
  currency: string
  task_count: number
  unknown_amount_count: number
  known_total: number
}

type TaskCost = {
  cost_basis: string
  total_cost?: number | null
  cost_currency: string
}

function amount(value: number) {
  return value.toLocaleString('zh-CN', { maximumFractionDigits: 6 })
}

export function taskCostSummaryText(row: TaskCostSummary) {
  if (row.unknown_amount_count >= row.task_count) return '金额未知 / 待对账'
  if (row.unknown_amount_count > 0) {
    return `已知部分 ${amount(row.known_total)} ${row.currency}；另有 ${row.unknown_amount_count} 项待对账`
  }
  return `${amount(row.known_total)} ${row.currency}`
}

export function taskCostText(row: TaskCost) {
  if (row.cost_basis === 'unknown' || row.total_cost === null || row.total_cost === undefined) {
    return '金额未知 / 待对账'
  }
  return `${amount(row.total_cost)} ${row.cost_currency}`
}
