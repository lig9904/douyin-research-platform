import assert from 'node:assert/strict'
import { canonicalSupplierDailySpendRows, formatSupplierDailyAmount, hasSupplierDailySpendRecord, supplierDailySpendFootnote, supplierDailySpendPeriodText, supplierDailySpendSummaryText } from '../src/dailySpendDisplay'

assert.equal(supplierDailySpendPeriodText({ period_status: 'current_accumulating' }), '当前账期')
assert.equal(supplierDailySpendPeriodText({ period_status: 'prior_snapshot' }), '历史账期')
assert.equal(hasSupplierDailySpendRecord([{ provider: 'tikhub' }], 'volcengine-billing'), false)
assert.equal(hasSupplierDailySpendRecord([{ provider: 'tikhub' }, { provider: 'volcengine-billing' }], 'volcengine-billing'), true)
assert.equal(hasSupplierDailySpendRecord([{ provider: 'volcengine-billing', scope_kind: 'product_subset' }], 'volcengine-billing', 'account_total'), false)
assert.equal(hasSupplierDailySpendRecord([{ provider: 'volcengine-billing', scope_kind: 'account_total' }], 'volcengine-billing', 'account_total'), true)
assert.equal(formatSupplierDailyAmount(0.000009), '0.000009')

assert.equal(supplierDailySpendFootnote(), '等待供应商账单同步')
assert.equal(supplierDailySpendFootnote({ status: 'not_synced', today: [], message: '同步失败' }), '同步失败')
assert.match(supplierDailySpendFootnote({ status: 'available', today: [] }), /尚无供应商当前账期记录/)
const rows = [
  { provider: 'tikhub', account_scope: 'default', scope_label: '账户总费用', billing_date: '2026-09-20', billing_timezone: 'America/Los_Angeles', billing_finality: 'preliminary' as const, freshness_status: 'fresh' as const },
  { provider: 'other', account_scope: 'second', scope_label: '模型产品', billing_date: '2026-09-21', billing_timezone: 'Asia/Shanghai', billing_finality: 'final' as const, freshness_status: 'stale' as const },
]
const text = supplierDailySpendFootnote({ status: 'available', today: rows })
const entries = text.split('；')
assert.equal(entries.length, 2)
assert.match(entries[0], /tikhub \[default \/ 账户总费用\].*2026-09-20（America\/Los_Angeles）/)
assert.doesNotMatch(entries[0], /过期|Shanghai/)
assert.match(entries[1], /other \[second \/ 模型产品\].*2026-09-21（Asia\/Shanghai）.*更新可能过期/)
assert.match(entries[0], /当日累计，未终账/)
assert.match(entries[1], /已终账/)
assert.doesNotMatch(entries[1], /未终账/)
assert.equal((text.match(/账户总费用/g) || []).length, 1)
assert.equal(supplierDailySpendFootnote({ status: 'available', today: [...rows].reverse() }), [...entries].reverse().join('；'))
const overlapping = [
  { ...rows[0], provider: 'volcengine-billing', account_scope: 'payer:123', billing_date: '2026-09-21', cost_currency: 'CNY', scope_kind: 'product_subset' as const, scope_label: 'ASR' },
  { ...rows[0], provider: 'volcengine-billing', account_scope: 'payer:123', billing_date: '2026-09-21', cost_currency: 'CNY', scope_kind: 'account_total' as const, scope_label: '付款账户总费用' },
  { ...rows[0], provider: 'volcengine-billing', account_scope: 'payer:123', billing_date: '2026-09-20', cost_currency: 'CNY', scope_kind: 'product_subset' as const, scope_label: '昨日ASR' },
]
assert.deepEqual(canonicalSupplierDailySpendRows(overlapping), [overlapping[1], overlapping[2]])
assert.doesNotMatch(supplierDailySpendFootnote({ status: 'available', today: overlapping }), /\/ ASR\]/)
assert.match(supplierDailySpendFootnote({ status: 'available', today: overlapping }), /昨日ASR/)
const summary = supplierDailySpendSummaryText([
  { ...overlapping[0], total_cost: 0.000009 },
  { ...overlapping[1], total_cost: 0.000009 },
  { ...overlapping[2], total_cost: null },
])
assert.match(summary, /付款账户总费用.*0\.000009/)
assert.doesNotMatch(summary, /\/ ASR\]/)
assert.match(summary, /昨日ASR.*金额待核验/)
assert.equal(supplierDailySpendSummaryText([]), '尚未同步供应商日账')
console.log('Daily spend billing-period display assertions passed')
