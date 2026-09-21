import React, { useEffect, useRef, useState } from 'react'
import { Alert, Button } from 'antd'
import { backend } from '../../backend'

export default function VideoMediaPreview({ videoId }: { videoId: string }) {
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [empty, setEmpty] = useState(false)
  const request = useRef(0)

  useEffect(() => {
    request.current += 1
    setUrl(''); setError(''); setEmpty(false); setBusy(false)
    return () => { request.current += 1 }
  }, [videoId])

  async function load() {
    const current = ++request.current
    setBusy(true); setUrl(''); setError(''); setEmpty(false)
    try {
      const result = await backend.video_media_preview({ video_id: videoId })
      if (current !== request.current) return
      if (result.has_media === false) { setEmpty(true); return }
      if (result.has_media !== true || typeof result.playback_url !== 'string' ||
          new URL(result.playback_url).protocol !== 'https:' || result.expires_in_seconds !== 300) {
        throw new Error('invalid playback response')
      }
      setUrl(result.playback_url)
    } catch {
      if (current === request.current) setError('视频暂不可播放，请确认访问权限或刷新链接。')
    } finally {
      if (current === request.current) setBusy(false)
    }
  }

  return <section className="private-video-preview" aria-label="私有视频预览">
    <div className="detail-section-head">
      <h4>视频预览</h4>
      <Button loading={busy} disabled={busy} onClick={load}>加载视频 / 刷新链接</Button>
    </div>
    {error && <Alert type="error" showIcon message={error} />}
    {empty && <p>尚无已入库视频，请先完成媒体处理。</p>}
    {!url && !empty && !error && <p>点击加载私有视频，不会触发采集或模型调用。</p>}
    {url && <video key={url} controls playsInline preload="metadata" src={url}
      aria-label="已入库视频播放器"
      onError={() => { setUrl(''); setError('视频播放失败或链接已过期，请刷新链接。') }} />}
    <small>私有播放链接有效期 5 分钟；播放不代表音频审核通过。</small>
  </section>
}
