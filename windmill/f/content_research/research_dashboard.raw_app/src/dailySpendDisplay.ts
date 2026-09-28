type DailySpendPeriod = {
  provider: string
  account_scope: string
  scope_kind?: 'account_total' | 'product_subset'
  cost_currency?: string
  scope_label: string
  billing_date: string
  billing_timezone: string
  billing_finality: 'preliminary' | 'final'
  freshness_status: 'fresh' | 'stale'
}

type DailySpendState = {
  status: string
  today: DailySpendPeriod[]
  message?: string | null
}

type DailySpendRecord = { provider: string; scope_kind?: 'account_total' | 'product_subset' }

export function hasSupplierDailySpendRecord(records: DailySpendRecord[], provider: string, scopeKind?: 'account_total' | 'product_subset'): boolean {
  return records.some((row) => row.provider === provider && (!scopeKind || row.scope_kind === scopeKind))
}

type ScopedSpend = {
  provider: string; account_scope: string; billing_date: string
  cost_currency?: string; scope_kind?: 'account_total' | 'product_subset'
}

// Product rows remain stored for traceability, but an account total already
// includes them. Never present both as additive daily amounts.
export function canonicalSupplierDailySpendRows<T extends ScopedSpend>(rows: T[]): T[] {
  const key = (row: T) => [row.provider, row.account_scope, row.billing_date, row.cost_currency || ''].join('\u0000')
  const accountDays = new Set(rows.filter((row) => row.scope_kind === 'account_total').map(key))
  return rows.filter((row) => row.scope_kind !== 'product_subset' || !accountDays.has(key(row)))
}

export function supplierDailySpendPeriodText(row: { period_status: 'current_accumulating' | 'prior_snapshot' }): string {
  return row.period_status === 'current_accumulating' ? '当前账期' : '历史账期'
}

export function formatSupplierDailyAmount(value: number | string): string {
  const amount = Number(value)
  return Number.isFinite(amount) ? amount.toLocaleString('zh-CN', { maximumFractionDigits: 6 }) : '待核验'
}

type SpendSummaryRow = ScopedSpend & { scope_label: string; total_cost?: number | null }

export function supplierDailySpendSummaryText(rows: SpendSummaryRow[]): string {
  const canonical = canonicalSupplierDailySpendRows(rows)
  if (!canonical.length) return '尚未同步供应商日账'
  return canonical.map((row) =>
    `${row.provider} [${row.account_scope} / ${row.scope_label}] ${row.billing_date} ${row.cost_currency || '币种待核验'} ${row.total_cost == null ? '金额待核验' : formatSupplierDailyAmount(row.total_cost)}`,
  ).join('；')
}

// Each supplier can use a different calendar day; never borrow the first
// supplier's date/timezone for every amount displayed in the summary.
export function supplierDailySpendFootnote(spend?: DailySpendState): string {
  if (!spend || spend.status !== 'available') return spend?.message || '等待供应商账单同步'
  if (!spend.today.length) return '供应商费用快照 · 尚无供应商当前账期记录'
  return canonicalSupplierDailySpendRows(spend.today).map((item) =>
    `${item.provider} [${item.account_scope} / ${item.scope_label}] · ${item.billing_date}（${item.billing_timezone}） · ${item.billing_finality === 'final' ? '已终账' : '当日累计，未终账'}${item.freshness_status === 'stale' ? ' · 更新可能过期' : ''}`,
  ).join('；')
}
