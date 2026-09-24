import React, { useState } from 'react'
import { Alert, Button, Checkbox, Tag } from 'antd'
import { backend } from '../../backend'

type Asset = {
  asset_id: string
  size_bytes: number
  content_sha256: string
  asset_manifest_fingerprint: string
  delivery_origin: string
  review_id?: string | null
  review_status: string
}

const reviewVersion = 'media-v1'
const consent = '我已核对音频内容并同意交由云端转写'
const labels: Record<string, string> = {
  not_reviewed: '待审核', approved: '已审核', revoked: '已撤销', stale: '资产已变化',
}

export default function ProjectASRMediaReviewPanel({ projectId, videoId }: {
  projectId: string
  videoId: string
}) {
  const [assets, setAssets] = useState<Asset[] | null>(null)
  const [selected, setSelected] = useState<Asset | null>(null)
  const [url, setUrl] = useState('')
  const [playable, setPlayable] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const refresh = async () => {
    setBusy(true); setError(''); setNotice(''); setSelected(null); setUrl('')
    setPlayable(false); setConfirmed(false)
    try {
      const result = await backend.project_asr_media_review({
        project_id: projectId, video_id: videoId, action: 'list', review_version: reviewVersion,
      }) as { assets: Asset[] }
      setAssets(result.assets)
    } catch {
      setError('项目音频列表不可用；请确认你具有本项目审核权限且视频已纳入项目。')
    } finally { setBusy(false) }
  }

  const playback = async (asset: Asset) => {
    setBusy(true); setError(''); setNotice(''); setUrl(''); setPlayable(false); setConfirmed(false)
    try {
      const result = await backend.project_asr_media_review({
        project_id: projectId, video_id: videoId, asset_id: asset.asset_id,
        action: 'playback', review_version: reviewVersion,
      }) as { playback_url: string; asset_manifest_fingerprint: string; review_status: string }
      if (result.asset_manifest_fingerprint !== asset.asset_manifest_fingerprint) {
        throw new Error('asset changed')
      }
      setSelected({ ...asset, review_status: result.review_status })
      setUrl(result.playback_url)
    } catch {
      setError('音频无法试听或已变化，请刷新列表后重试。')
    } finally { setBusy(false) }
  }

  const approve = async () => {
    if (!selected || !playable || !confirmed || selected.review_status !== 'not_reviewed') return
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await backend.project_asr_media_review({
        project_id: projectId, video_id: videoId, asset_id: selected.asset_id,
        action: 'approve', review_version: reviewVersion,
        expected_manifest_fingerprint: selected.asset_manifest_fingerprint,
        consent_statement: consent,
      }) as { status: string; review_id: string }
      if (result.status !== 'approved') throw new Error('not saved')
      setSelected({ ...selected, review_status: 'approved', review_id: result.review_id })
      setAssets(items => items?.map(item => item.asset_id === selected.asset_id
        ? { ...item, review_status: 'approved', review_id: result.review_id } : item) ?? null)
      setConfirmed(false)
      setNotice('本项目的音频审核已保存；尚未提交云端转写，也没有产生 ASR 费用。')
    } catch {
      setError('审核未保存，资产或权限可能已变化；请重新试听并刷新。')
    } finally { setBusy(false) }
  }

  const revoke = async (asset: Asset) => {
    if (!asset.review_id) return
    setBusy(true); setError(''); setNotice('')
    try {
      await backend.project_asr_media_review({
        project_id: projectId, video_id: videoId, action: 'revoke',
        media_review_id: asset.review_id,
      })
      setSelected(null); setUrl(''); setPlayable(false); setConfirmed(false)
      setAssets(items => items?.map(item => item.asset_id === asset.asset_id
        ? { ...item, review_status: 'revoked' } : item) ?? null)
      setNotice('审核已撤销；项目转写与依赖它的 L3 结果不再展示，也不能据此发起新调用。')
    } catch {
      setError('撤销未完成，请刷新审核状态后重试。')
    } finally { setBusy(false) }
  }

  return <section className="detail-section" aria-label="项目音频与转写审核">
    <div className="detail-section-head"><h4>本项目音频审核</h4>
      <Button loading={busy} onClick={refresh}>加载音频</Button></div>
    <p className="muted">只对本项目生效；试听和批准不会调用付费转写。请核对内容及云端交付范围。</p>
    {error && <Alert type="error" showIcon message={error} />}
    {notice && <Alert type="success" showIcon message={notice} />}
    {assets?.length === 0 && <p className="muted">本视频尚无可审核 WAV 音频；需先完成受控媒体入库。</p>}
    {assets?.map(asset => <div key={asset.asset_id} style={{ margin: '12px 0' }}>
      <Tag>{labels[asset.review_status] || '状态未知'}</Tag>
      <span>WAV · {(asset.size_bytes / 1024).toFixed(1)} KB · {asset.content_sha256.slice(0, 12)}</span>{' '}
      <Button disabled={busy} onClick={() => playback(asset)}>试听</Button>{' '}
      {asset.review_status === 'approved' && asset.review_id
        ? <Button danger disabled={busy} onClick={() => revoke(asset)}>撤销审核</Button> : null}
    </div>)}
    {selected && url && <div>
      <audio controls preload="metadata" src={url}
        onLoadedMetadata={() => setPlayable(true)}
        onError={() => { setPlayable(false); setConfirmed(false); setError('试听失败或链接已过期，请刷新链接。') }} />
      <p className="muted">链接有效期 5 分钟 · 云端读取域名：{selected.delivery_origin}</p>
      {selected.review_status === 'not_reviewed' && <>
        <Checkbox checked={confirmed} disabled={!playable || busy}
          onChange={event => setConfirmed(event.target.checked)}>{consent}</Checkbox>{' '}
        <Button type="primary" disabled={!playable || !confirmed || busy} onClick={approve}>保存本项目审核</Button>
      </>}
    </div>}
  </section>
}
