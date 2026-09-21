import React from 'react'
import assert from 'node:assert/strict'
import { renderToStaticMarkup } from 'react-dom/server'
import ASRTranscriptPanel, { type ASRTranscript } from '../src/components/ASRTranscriptPanel'

const sample: ASRTranscript = {
  text: '<script>alert(1)</script>真实转写', truncated: false,
  quality_status: 'usable', provider: 'volcengine-doubao-asr',
  model_id: 'bigmodel', model_revision: '2.0', engine_version: 'volc.seedasr.auc',
  audio_duration_ms: 28723, cost: {asr_cost: null, currency: 'CNY', basis: 'unknown'},
}
const empty = renderToStaticMarkup(<ASRTranscriptPanel />)
assert.match(empty, /尚无已完成的转写/)
const result = renderToStaticMarkup(<ASRTranscriptPanel transcript={sample} />)
assert.match(result, /未知 \/ 待对账/)
assert.match(result, /28.7 秒/)
assert.match(result, /机器判定可用 · 待人工核对/)
assert.match(result, /&lt;script&gt;/)
assert.doesNotMatch(result, /<script>/)
assert.doesNotMatch(result, /CNY 0/)
const zero = renderToStaticMarkup(<ASRTranscriptPanel transcript={{...sample, truncated: true, cost: {asr_cost: 0, currency: 'CNY', basis: 'actual'}}} />)
assert.match(zero, /CNY 0/)
assert.match(zero, /仅展示前 12000 个字符/)
console.log('ASRTranscriptPanel rendering checks passed')
