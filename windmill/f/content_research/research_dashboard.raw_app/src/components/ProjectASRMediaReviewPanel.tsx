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
  authorization_kind?: 'listened' | 'standing_grant'
}
type StandingGrant = { id: string; provider: string; source_scope: string; authorized_at: string }

const reviewVersion = 'media-v1'
const consent = '我已核对音频内容并同意交由云端转写'
const standingConsent = '我授权本项目已纳入的公开视频音频交火山云端转写；未逐条核听，可随时撤销'
const labels: Record<string, string> = {
  not_reviewed: '待审核', approved: '已审核', revoked: '已撤销', stale: '资产已变化', grant_revoked: '持续授权已撤销',
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
  const [grant, setGrant] = useState<StandingGrant | null>(null)
  const [standingConfirmed, setStandingConfirmed] = useState(false)
  const [canManageStanding, setCanManageStanding] = useState(false)

  const refresh = async () => {
    setBusy(true); setError(''); setNotice(''); setSelected(null); setUrl('')
    setPlayable(false); setConfirmed(false)
    try {
      const result = await backend.project_asr_media_review({
        project_id: projectId, video_id: videoId, action: 'list', review_version: reviewVersion,
      }) as { assets: Asset[] }
      setAssets(result.assets)
      try {
        const standing = await backend.project_asr_media_review({
          project_id: projectId, video_id: videoId, action: 'standing_status',
        }) as { grant: StandingGrant | null }
        setGrant(standing.grant); setCanManageStanding(true)
      } catch {
        // A global reviewer may inspect and review one asset without being a
        // project owner; the project-wide grant control must not hide assets.
        setGrant(null); setCanManageStanding(false)
      }
    } catch {
      setError('项目音频列表不可用；请确认你具有本项目审核权限且视频已纳入项目。')
    } finally { setBusy(false) }
  }

  const createStanding = async () => {
    if (!standingConfirmed || grant) return
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await backend.project_asr_media_review({
        project_id: projectId, video_id: videoId, action: 'standing_create',
        consent_statement: standingConsent,
      }) as { grant: StandingGrant }
      setGrant(result.grant); setStandingConfirmed(false)
      setNotice('本项目持续转写授权已保存；不表示任何音频已人工核听。定时派发仅处理符合项目范围的音频。')
    } catch { setError('持续授权未保存，请核对项目负责人权限。') }
    finally { setBusy(false) }
  }

  const revokeStanding = async () => {
    if (!grant) return
    setBusy(true); setError(''); setNotice('')
    try {
      await backend.project_asr_media_review({
        project_id: projectId, video_id: videoId, action: 'standing_revoke', standing_grant_id: grant.id,
      })
      setGrant(null)
      await refresh()
      setNotice('持续授权已撤销；不会再因此发起新的转写。已提交供应商的任务可能仍会结算费用。')
    } catch { setError('持续授权撤销未完成，请刷新后核对。') }
    finally { setBusy(false) }
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
    <p className="muted">只对本项目生效；单纯试听或保存单条审核不会立即调用付费转写。项目持续授权生效时，已接受视频的入库音频可能由计划任务自动送交火山转写；请核对内容及云端交付范围。</p>
    {canManageStanding && (grant ? <p><Tag color="blue">项目持续授权生效</Tag>火山转写 · 已纳入且可用的公开视频 · 未逐条核听{' '}
      <Button danger disabled={busy} onClick={revokeStanding}>撤销持续授权</Button></p>
      : <div><Checkbox checked={standingConfirmed} disabled={busy}
          onChange={event => setStandingConfirmed(event.target.checked)}>{standingConsent}</Checkbox>{' '}
        <Button disabled={!standingConfirmed || busy} onClick={createStanding}>保存项目持续授权</Button></div>)}
    {error && <Alert type="error" showIcon message={error} />}
    {notice && <Alert type="success" showIcon message={notice} />}
    {assets?.length === 0 && <p className="muted">本视频尚无可审核 WAV 音频；需先完成受控媒体入库。</p>}
    {assets?.map(asset => <div key={asset.asset_id} style={{ margin: '12px 0' }}>
      <Tag>{asset.authorization_kind === 'standing_grant' && asset.review_status === 'approved'
        ? '持续授权·未人工核听' : labels[asset.review_status] || '状态未知'}</Tag>
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
