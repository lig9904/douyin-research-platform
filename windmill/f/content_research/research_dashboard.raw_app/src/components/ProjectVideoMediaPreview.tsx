import React, { useEffect, useRef, useState } from 'react'
import { Alert, Button } from 'antd'
import { backend } from '../../backend'

type Preview = { has_media: boolean; playback_url?: string; expires_in_seconds?: number }
export type VideoSeekRequest = { projectId: string; videoId: string; seconds: number; sequence: number }

export default function ProjectVideoMediaPreview({ projectId, videoId, seekRequest }: {
  projectId: string
  videoId: string
  seekRequest?: VideoSeekRequest | null
}) {
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [empty, setEmpty] = useState(false)
  const [seekNotice, setSeekNotice] = useState('')
  const request = useRef(0)
  const activeUrl = useRef('')
  const player = useRef<HTMLVideoElement>(null)
  const targetSecond = useRef<number | null>(null)
  const handledSeek = useRef(0)

  useEffect(() => {
    request.current += 1
    activeUrl.current = ''
    targetSecond.current = null
    setUrl(''); setError(''); setEmpty(false); setBusy(false); setSeekNotice('')
    return () => { request.current += 1 }
  }, [projectId, videoId])

  async function load() {
    const current = ++request.current
    activeUrl.current = ''
    setBusy(true); setUrl(''); setError(''); setEmpty(false)
    try {
      const result = await backend.project_video_media_preview({
        project_id: projectId, video_id: videoId,
      }) as Preview
      if (current !== request.current) return
      if (result.has_media === false) { setEmpty(true); return }
      if (result.has_media !== true || typeof result.playback_url !== 'string' ||
          new URL(result.playback_url).protocol !== 'https:' ||
          result.expires_in_seconds !== 300) {
        throw new Error('invalid playback response')
      }
      activeUrl.current = result.playback_url
      setUrl(result.playback_url)
    } catch {
      if (current === request.current) setError('视频暂不可播放，请确认项目权限或刷新链接。')
    } finally {
      if (current === request.current) setBusy(false)
    }
  }

  function applySeek() {
    const video = player.current
    const seconds = targetSecond.current
    if (!video || seconds === null || video.readyState < 1) return
    targetSecond.current = null
    if (!Number.isFinite(video.duration) || seconds >= video.duration) {
      setSeekNotice('机器时间码超出原片长度，请核对原始结果。')
      return
    }
    video.currentTime = seconds
    video.scrollIntoView?.({ behavior: 'smooth', block: 'center' })
    setSeekNotice(`已定位到 ${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}；请自行播放并核对画面与声音。`)
  }

  useEffect(() => {
    if (!seekRequest || seekRequest.projectId !== projectId || seekRequest.videoId !== videoId ||
        seekRequest.sequence <= handledSeek.current) return
    handledSeek.current = seekRequest.sequence
    if (!Number.isFinite(seekRequest.seconds) || seekRequest.seconds < 0 || seekRequest.seconds >= 86400) return
    targetSecond.current = seekRequest.seconds
    setSeekNotice('正在定位到原片；这不表示该片段已核看。')
    if (!url) void load()
    else applySeek()
  }, [seekRequest])

  return <section className="private-video-preview" aria-label="本项目视频预览">
    <div className="detail-section-head">
      <h4>本项目视频预览</h4>
      <Button loading={busy} disabled={busy} onClick={() => { targetSecond.current = null; void load() }}>加载视频 / 刷新链接</Button>
    </div>
    {error && <Alert type="error" showIcon message={error} />}
    {seekNotice && <Alert type="info" showIcon message={seekNotice} />}
    {empty && <p>尚无已入库视频，请先完成媒体处理。</p>}
    {!url && !empty && !error && <p>仅播放本项目已纳入的公开视频副本；不会触发采集或模型调用。</p>}
    {url && <video key={url} ref={player} controls playsInline preload="metadata" src={url}
      aria-label="本项目已入库视频播放器"
      onLoadedMetadata={applySeek}
      onError={() => {
        if (activeUrl.current !== url) return
        activeUrl.current = ''
        setUrl(''); setError('视频播放失败或链接已过期，请刷新链接。')
      }} />}
    <small>私有播放链接有效期 5 分钟；跨项目共享不包含媒体，播放不代表内容已审核。</small>
  </section>
}
