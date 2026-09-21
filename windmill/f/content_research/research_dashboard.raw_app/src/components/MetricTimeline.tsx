import React, { useEffect, useState } from 'react'
import { Alert, Button, Pagination, Select, Spin } from 'antd'
import { backend } from '../../backend'

type Metric = {
  captured_at: string
  source_kind?: 'billboard' | 'detail' | 'other'
  play_count?: number | null
  like_count?: number | null
  comment_count?: number | null
  share_count?: number | null
  collect_count?: number | null
  author_follower_count?: number | null
}

type Timeline = { total: number; page: number; page_size: number; items: Metric[] }

function count(value?: number | null) {
  return value == null ? '—' : Number(value).toLocaleString('zh-CN')
}

function time(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(date)
}

export default function MetricTimeline({ videoId }: { videoId: string }) {
  const [days, setDays] = useState(30)
  const [data, setData] = useState<Timeline | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = async (page = 1, nextDays = days) => {
    setLoading(true)
    setError('')
    try {
      setData(await backend.get_video_metric_timeline({
        video_id: videoId, days: nextDays, page, page_size: 20,
      }) as Timeline)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '指标历史暂不可用。')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load(1) }, [videoId])

  return (
    <section className="detail-section metric-timeline-section">
      <div className="detail-section-head">
        <h4>指标历史</h4>
        <Select
          value={days}
          size="small"
          onChange={(next) => { setDays(next); load(1, next) }}
          options={[{ value: 7, label: '近 7 天' }, { value: 30, label: '近 30 天' }, { value: 90, label: '近 90 天' }]}
        />
      </div>
      <p className="metric-timeline-note">仅展示规范化指标快照；不同来源的统计口径可能不同，不能直接视为增长或下降。0 为接口记录值，不代表已核实真实为零；— 表示缺失。不展示原始响应或请求标识。</p>
      {error && <Alert type="error" showIcon message="指标历史未加载" description={error} />}
      {loading ? <div className="metric-timeline-loading"><Spin size="small" /></div> : (
        <>
          <div className="metric-timeline-table-wrap">
            <table className="metric-timeline-table">
              <thead><tr><th>采集时间</th><th>来源</th><th>播放</th><th>点赞</th><th>评论</th><th>分享</th><th>收藏</th><th>粉丝</th></tr></thead>
              <tbody>
                {(data?.items || []).map((item, index) => (
                  <tr key={`${item.captured_at}-${index}`}>
                    <td>{time(item.captured_at)}</td><td>{item.source_kind === 'billboard' ? '榜单' : item.source_kind === 'detail' ? '详情' : '其他 / 未标注'}</td><td>{count(item.play_count)}</td><td>{count(item.like_count)}</td>
                    <td>{count(item.comment_count)}</td><td>{count(item.share_count)}</td><td>{count(item.collect_count)}</td>
                    <td>{count(item.author_follower_count)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!data?.items.length && <p className="muted">所选时间范围内暂无指标快照。</p>}
          {(data?.total || 0) > (data?.page_size || 20) && (
            <Pagination size="small" current={data?.page || 1} pageSize={data?.page_size || 20} total={data?.total || 0}
              showSizeChanger={false} onChange={(page) => load(page)} />
          )}
        </>
      )}
    </section>
  )
}
