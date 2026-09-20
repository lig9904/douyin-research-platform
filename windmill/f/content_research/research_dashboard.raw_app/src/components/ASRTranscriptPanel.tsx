import React from 'react'
import { Tag } from 'antd'

export type ASRTranscript = {
  text: string
  truncated: boolean
  quality_status: string
  provider: string
  model_id?: string | null
  model_revision?: string | null
  engine_version?: string | null
  language?: string | null
  audio_duration_ms?: number | null
  created_at?: string | null
  cost: {
    api_cost?: number | null
    asr_cost?: number | null
    llm_cost?: number | null
    total_cost?: number | null
    currency?: string | null
    basis?: string | null
  }
}

const qualityLabels: Record<string, string> = {
  usable: '机器判定可用 · 待人工核对', unreviewed: '未核对',
  low_confidence: '低置信度', no_speech: '未识别人声', rejected: '已拒绝',
}
const basisLabels: Record<string, string> = {
  actual: '接口记录实际费用', estimated: '估算费用', mixed: '实际与估算混合', unknown: '未知 / 待对账',
}

export default function ASRTranscriptPanel({ transcript }: { transcript?: ASRTranscript | null }) {
  const amount = transcript?.cost.asr_cost
  const knownCost = typeof amount === 'number' && Number.isFinite(amount)
  return <section className="detail-section" aria-label="已完成音频转写">
    <div className="detail-section-head"><h4>音频转写结果</h4>
      {transcript && <Tag>{qualityLabels[transcript.quality_status] || '状态未知'}</Tag>}
    </div>
    {!transcript ? <p className="muted">尚无已完成的转写。音频审核通过不代表转写已经完成。</p> : <>
      <div className="l3-version-grid">
        <span>服务 <b>{transcript.provider}</b></span>
        <span>模型 <b>{transcript.model_id || '未记录'}</b></span>
        <span>版本 <b>{transcript.model_revision || '未记录'}</b></span>
        <span>引擎 <b>{transcript.engine_version || '未记录'}</b></span>
        <span>语言 <b>{transcript.language || '未记录'}</b></span>
        <span>时长 <b>{transcript.audio_duration_ms == null ? '未知' : `${(transcript.audio_duration_ms / 1000).toFixed(1)} 秒`}</b></span>
      </div>
      <p>ASR 费用：{knownCost ? `${transcript.cost.currency || '币种未记录'} ${amount}` : '未知 / 待对账'}
        {' · '}{basisLabels[transcript.cost.basis || 'unknown'] || '口径未记录'}</p>
      <div style={{whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 320, overflowY: 'auto'}}>
        {transcript.text || '本次未返回识别文本。'}
      </div>
      {transcript.truncated && <p>正文超过页面展示上限，仅展示前 12000 个字符；完整记录仍保存在数据库。</p>}
      <p className="muted">以上为机器识别原文，可能包含错字或误识别，请对照音频核对。机器判定可用不代表人工验收通过；未知费用不等于免费。</p>
    </>}
  </section>
}
