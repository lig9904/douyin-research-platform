import assert from 'node:assert/strict'
import { supplierDailySpendFootnote } from '../src/dailySpendDisplay'

assert.equal(supplierDailySpendFootnote(), '等待供应商账单同步')
assert.equal(supplierDailySpendFootnote({ status: 'not_synced', today: [], message: '同步失败' }), '同步失败')
assert.match(supplierDailySpendFootnote({ status: 'available', today: [] }), /尚无供应商当前账期记录/)
const rows = [
  { provider: 'tikhub', account_scope: 'default', billing_date: '2026-09-20', billing_timezone: 'America/Los_Angeles', freshness_status: 'fresh' as const },
  { provider: 'other', account_scope: 'second', billing_date: '2026-09-21', billing_timezone: 'Asia/Shanghai', freshness_status: 'stale' as const },
]
const text = supplierDailySpendFootnote({ status: 'available', today: rows })
const entries = text.split('；')
assert.equal(entries.length, 2)
assert.match(entries[0], /tikhub \[default\].*2026-09-20（America\/Los_Angeles）/)
assert.doesNotMatch(entries[0], /过期|Shanghai/)
assert.match(entries[1], /other \[second\].*2026-09-21（Asia\/Shanghai）.*更新可能过期/)
assert.ok(entries.every(x => x.includes('未终账')))
assert.doesNotMatch(text, /账户总费用/)
assert.equal(supplierDailySpendFootnote({ status: 'available', today: [...rows].reverse() }), [...entries].reverse().join('；'))
console.log('Daily spend billing-period display assertions passed')
