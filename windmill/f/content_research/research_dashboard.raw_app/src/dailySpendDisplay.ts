type DailySpendPeriod = {
  provider: string
  account_scope: string
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

// Each supplier can use a different calendar day; never borrow the first
// supplier's date/timezone for every amount displayed in the summary.
export function supplierDailySpendFootnote(spend?: DailySpendState): string {
  if (!spend || spend.status !== 'available') return spend?.message || '等待供应商账单同步'
  if (!spend.today.length) return '供应商费用快照 · 尚无供应商当前账期记录'
  return spend.today.map((item) =>
    `${item.provider} [${item.account_scope} / ${item.scope_label}] · ${item.billing_date}（${item.billing_timezone}） · ${item.billing_finality === 'final' ? '已终账' : '当日累计，未终账'}${item.freshness_status === 'stale' ? ' · 更新可能过期' : ''}`,
  ).join('；')
}
