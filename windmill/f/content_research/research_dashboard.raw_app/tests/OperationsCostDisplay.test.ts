import assert from 'node:assert/strict'
import { taskCostSummaryText, taskCostText } from '../src/operationsCostDisplay'

assert.equal(taskCostText({ cost_basis: 'unknown', total_cost: null, cost_currency: 'CNY' }), '金额未知 / 待对账')
assert.equal(taskCostText({ cost_basis: 'unknown', total_cost: 0, cost_currency: 'CNY' }), '金额未知 / 待对账')
assert.equal(taskCostText({ cost_basis: 'actual', total_cost: 0, cost_currency: 'CNY' }), '0 CNY')
assert.equal(taskCostText({ cost_basis: 'estimated', total_cost: 0.005883, cost_currency: 'CNY' }), '0.005883 CNY')
assert.equal(taskCostText({ cost_basis: 'mixed', total_cost: null, api_cost: 0.01, asr_cost: null, llm_cost: 0, cost_currency: 'CNY' }), '已知部分 0.01 CNY；其余待对账')
assert.equal(taskCostSummaryText({ currency: 'CNY', task_count: 2, unknown_amount_count: 2, known_total: 0 }), '金额未知 / 待对账')
assert.equal(taskCostSummaryText({ currency: 'CNY', task_count: 1, unknown_amount_count: 1, known_total: 0.01 }), '已知部分 0.01 CNY；另有 1 项待对账')
assert.equal(taskCostSummaryText({ currency: 'CNY', task_count: 2, unknown_amount_count: 1, known_total: 0.5 }), '已知部分 0.5 CNY；另有 1 项待对账')
assert.equal(taskCostSummaryText({ currency: 'CNY', task_count: 2, unknown_amount_count: 0, known_total: 0 }), '0 CNY')
console.log('Operations cost display assertions passed')
