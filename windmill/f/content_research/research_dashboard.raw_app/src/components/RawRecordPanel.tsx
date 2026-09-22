import React, { useState } from 'react'
import { Alert, Button, Spin, Tag } from 'antd'
import { backend } from '../../backend'

type RawJson = { json_text: string; truncated: boolean; stored_chars: number } | null
type RawRow = Record<string, unknown> & {
  raw_payload?: RawJson
  raw_metrics?: RawJson
  metadata?: RawJson
}

type RawRecords = {
  video_id: string
  canonical: Record<string, unknown>
  provider_snapshots: RawRow[]
  metric_snapshots: RawRow[]
  discoveries: RawRow[]
  comments: RawRow[]
  limits: Record<string, number>
  read_only: boolean
}

function rawText(value: unknown) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return JSON.stringify(value, null, 2)
  }
  const expanded = Object.fromEntries(Object.entries(value).map(([key, item]) => {
    if (item && typeof item === 'object' && 'json_text' in item) {
      const raw = item as RawJson
      if (!raw) return [key, null]
      try {
        return [key, {
          value: JSON.parse(raw.json_text),
          truncated: raw.truncated,
          stored_chars: raw.stored_chars,
        }]
      } catch {
        return [key, item]
      }
    }
    return [key, item]
  }))
  return JSON.stringify(expanded, null, 2)
}

function RawRows({ title, rows }: { title: string; rows: RawRow[] }) {
  return (
    <section className="raw-record-section">
      <h5>{title} <Tag>{rows.length} 条</Tag></h5>
      {!rows.length && <p className="muted">没有已保存记录。</p>}
      {rows.map((row, index) => (
        <details key={`${title}-${index}`}>
          <summary>第 {index + 1} 条 · {String(row.captured_at || row.discovered_at || row.published_at || '')}</summary>
          <pre>{rawText(row)}</pre>
        </details>
      ))}
    </section>
  )
}

export default function RawRecordPanel({ videoId }: { videoId: string }) {
  const [open, setOpen] = useState(false)
  const [loadedFor, setLoadedFor] = useState('')
  const [data, setData] = useState<RawRecords | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const load = async () => {
    setOpen(true)
    if (loadedFor === videoId && data) return
    setLoading(true)
    setError('')
    try {
      const result = await backend.get_video_raw_records({ video_id: videoId }) as RawRecords
      setData(result)
      setLoadedFor(videoId)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <section className="detail-section raw-record-panel">
      <div className="detail-section-head">
        <div>
          <h4>原始记录</h4>
          <span>数据库已保存的来源快照与原始字段；只在展开时读取</span>
        </div>
        <Button onClick={() => open ? setOpen(false) : load()}>
          {open ? '收起原始记录' : '展开原始记录'}
        </Button>
      </div>
      {open && (
        <Spin spinning={loading}>
          <Alert
            type="warning"
            showIcon
            message="这些是审计用原始数据，不等于最终合并口径。"
            description="敏感键会脱敏，单个 JSON 字段最多显示 100,000 个字符；MCP 不返回本区域正文。"
          />
          {error && <Alert type="error" showIcon message="原始记录加载失败" description={error} />}
          {data && loadedFor === videoId && (
            <div className="raw-record-content">
              <section className="raw-record-section">
                <h5>规范记录</h5>
                <pre>{rawText(data.canonical)}</pre>
              </section>
              <RawRows title="Provider 视频快照" rows={data.provider_snapshots} />
              <RawRows title="指标快照" rows={data.metric_snapshots} />
              <RawRows title="发现记录" rows={data.discoveries} />
              <RawRows title="评论原始行" rows={data.comments} />
            </div>
          )}
        </Spin>
      )}
    </section>
  )
}
