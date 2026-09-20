import React, { useEffect, useState } from 'react'
import { Alert, Button, Input, Pagination, Select, Spin, Tag } from 'antd'
import { backend } from '../../backend'
import PlatformIcon from './PlatformIcon'

type Platform = { key: string; name: string; enabled: boolean }

type SearchItem = {
  kind: 'video' | 'signal'
  id: string
  title: string
  subtitle?: string | null
  excerpt?: string | null
  platform: string
  observed_at?: string | null
  research_level?: number | null
  signal_type?: string | null
}

type SearchResult = {
  query: string
  platform: string
  page: number
  page_size: number
  total: number
  items: SearchItem[]
  searched_fields: string[]
  excluded_fields: string[]
}

function formatDate(value?: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(date)
}

export default function GlobalSearch({
  initialQuery,
  platforms,
  onNavigate,
  onOpenVideo,
}: {
  initialQuery: string
  platforms: Platform[]
  onNavigate: (view: 'home' | 'videos' | 'accounts' | 'hotspots') => void
  onOpenVideo: (videoId: string) => void
}) {
  const [query, setQuery] = useState(initialQuery)
  const [platform, setPlatform] = useState('all')
  const [result, setResult] = useState<SearchResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const search = async (page = 1) => {
    const value = query.trim()
    if (!value) {
      setResult(null)
      return
    }
    setLoading(true)
    setError('')
    try {
      const next = await backend.search_research({ query: value, platform, page, page_size: 20 }) as SearchResult
      setResult(next)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '搜索暂不可用，请稍后重试。')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (initialQuery.trim()) search(1)
  }, [])

  return (
    <div className="global-search-page">
      <div className="global-search-bar card">
        <Input.Search
          autoFocus
          value={query}
          placeholder="搜索视频标题、说明、账号昵称、热点或话题…"
          onChange={(event) => setQuery(event.target.value)}
          onSearch={() => search(1)}
          enterButton="搜索"
          allowClear
        />
        <Select
          value={platform}
          onChange={(value) => { setPlatform(value); setResult(null) }}
          options={[
            { value: 'all', label: '全部平台' },
            ...platforms.filter((item) => item.enabled).map((item) => ({ value: item.key, label: item.name })),
          ]}
        />
        <Button onClick={() => onNavigate('home')}>返回首页</Button>
      </div>
      <Alert
        className="global-search-notice"
        type="info"
        showIcon
        message="搜索范围经过隐私边界限制"
        description="搜索视频标题、说明、账号昵称和热点元数据；不搜索逐字稿、人工标注或 Provider 原始响应。"
      />
      {error && <Alert className="global-search-notice" type="error" showIcon message="搜索未完成" description={error} />}
      {loading && <div className="global-search-loading"><Spin /></div>}
      {!loading && result && (
        <section className="global-search-results card">
          <div className="global-search-results-head">
            <div><strong>“{result.query}”</strong> 共找到 {result.total} 条结果</div>
            <small>第 {result.page} 页</small>
          </div>
          <div className="global-search-list">
            {result.items.map((item) => (
              <article key={`${item.kind}-${item.id}`} className="global-search-item">
                <div className={`global-search-kind ${item.kind}`}>{item.kind === 'video' ? '视频' : '热点'}</div>
                <div className="global-search-item-copy">
                  <h2>{item.title}</h2>
                  <p>{item.excerpt || '暂无可展示说明。'}</p>
                  <div><PlatformIcon platform={item.platform} className="platform-mini" /><Tag>{item.platform}</Tag><span>{item.subtitle || '—'}</span><time>{formatDate(item.observed_at)}</time></div>
                </div>
                {item.kind === 'video' ? (
                  <Button type="link" onClick={() => onOpenVideo(item.id)}>查看案例</Button>
                ) : (
                  <Button type="link" onClick={() => onNavigate('hotspots')}>查看热点库</Button>
                )}
              </article>
            ))}
            {!result.items.length && <div className="global-search-empty">未找到匹配的公开研究元数据。</div>}
          </div>
          {result.total > result.page_size && (
            <Pagination
              current={result.page}
              pageSize={result.page_size}
              total={result.total}
              showSizeChanger={false}
              onChange={(page) => search(page)}
            />
          )}
        </section>
      )}
    </div>
  )
}
