import React, { useState } from 'react'
import { Alert, Button, Checkbox, Tag } from 'antd'
import { backend } from '../../backend'

type Asset = {
  asset_id: string; asset_fingerprint: string; size_bytes: number
  content_sha256: string; review_status: string; delivery_origin: string
}
const version = 'media-v1'
const labels: Record<string, string> = {
  not_reviewed: '未审核', approved: '已审核', revoked: '已撤销', stale: '资产已变化',
}

export default function ASRMediaReviewPanel({ videoId }: { videoId: string }) {
  const [assets, setAssets] = useState<Asset[] | null>(null)
  const [selected, setSelected] = useState<Asset | null>(null)
  const [url, setUrl] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [playable, setPlayable] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const canApprove = !!selected && selected.review_status === 'not_reviewed' && confirmed && playable && !busy

  async function load() {
    setBusy(true); setError(''); setNotice(''); setSelected(null); setUrl(''); setConfirmed(false)
    setPlayable(false)
    try {
      const result = await backend.asr_media_review({video_id: videoId, action: 'list', review_version: version})
      setAssets(result.assets)
    } catch { setError('音频列表不可用，请确认审核权限与媒体入库状态。') }
    finally { setBusy(false) }
  }
  async function play(asset: Asset) {
    setBusy(true); setError(''); setNotice(''); setUrl(''); setSelected(null); setConfirmed(false)
    setPlayable(false)
    try {
      const result = await backend.asr_media_review({video_id: videoId, asset_id: asset.asset_id, action: 'playback', review_version: version})
      setSelected(result); setUrl(result.playback_url)
    } catch { setError('音频暂不可播放，请检查对象存储或刷新列表。') }
    finally { setBusy(false) }
  }
  async function approve() {
    if (!selected || !canApprove) return
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await backend.asr_media_review({video_id: videoId, asset_id: selected.asset_id,
        action: 'approve', review_version: version, expected_asset_fingerprint: selected.asset_fingerprint})
      if (result.status !== 'approved') throw new Error('not saved')
      setAssets((items) => items?.map((a) => a.asset_id === selected.asset_id ? {...a, review_status: 'approved'} : a) ?? null)
      setSelected({...selected, review_status: 'approved'}); setConfirmed(false)
      setNotice('审核记录已保存。此操作没有提交转写，也没有调用模型。')
    } catch { setError('审核未保存，资产或权限可能已变化，请重新加载音频。') }
    finally { setBusy(false) }
  }
  return <section className="detail-section">
    <div className="detail-section-head"><h4>音频与转写审核</h4>
      <Button onClick={load} loading={busy}>加载音频</Button></div>
    <p>确认音频内容适合发送到云端转写后再批准。审核不等于转写完成。</p>
    {error && <Alert type="error" message={error} showIcon />}
    {notice && <Alert type="success" message={notice} showIcon />}
    {assets?.length === 0 && <p>尚无已入库音频，请先完成媒体处理。</p>}
    {assets?.map((asset) => <div key={asset.asset_id} style={{margin: '12px 0'}}>
      <Tag>{labels[asset.review_status] || '状态未知'}</Tag>
      <span>WAV · {(asset.size_bytes / 1024).toFixed(1)} KB · {asset.content_sha256.slice(0, 12)}</span>{' '}
      <Button disabled={busy} onClick={() => play(asset)}>试听 / 刷新链接</Button>
    </div>)}
    {selected && url && <div>
      <audio controls preload="metadata" src={url} onLoadedMetadata={() => setPlayable(true)} onError={() => {setPlayable(false); setConfirmed(false); setError('播放失败或链接已过期，请刷新链接。')}} />
      <p>链接有效期 5 分钟；云端读取地址：{selected.delivery_origin}</p>
      {selected.review_status === 'stale' && <Alert type="warning" message="资产已变化，当前审核版本不可再次批准，请联系管理员核对并更新审核版本。" />}
      <Checkbox checked={confirmed} disabled={!playable || busy || selected.review_status !== 'not_reviewed'} onChange={(e) => setConfirmed(e.target.checked)}>
        我已核对音频内容并同意交由云端转写
      </Checkbox>{' '}
      <Button disabled={!canApprove} onClick={approve}>保存审核</Button>
    </div>}
  </section>
}
