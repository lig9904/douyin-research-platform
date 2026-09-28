import React, { useEffect, useRef, useState } from 'react'
import { Alert, Button, Checkbox, Input, Modal, Tag } from 'antd'
import { backend } from '../../backend'

type Asset = {
  asset_id: string
  asset_sha256: string
  size_bytes: number
  asset_manifest_fingerprint: string | null
  review_id: string | null
  review_status: string
  machine_authorization_id: string | null
  machine_authorization_status: string
}
type Run = {
  attempt_id: string
  status: string
  record_mode?: string
  review_bound?: boolean
  machine_authorization_bound?: boolean
  object_version_bound?: boolean
  error_code: string | null
  provider_task_ref: string | null
  receipt_id: string | null
  estimated_cost: string | null
  cost_currency: string | null
  final_summary: string | null
  provenance: string
}
type PlaybackCandidate = {
  assetId: string
  reviewVersion: string
  objectVersionId: string
  manifestFingerprint: string
}

const consent = '我已核对这份完整视频并同意交由火山 LAS 进行音画分析'
const machineConsent = '我授权将这份视频交由火山 LAS 进行机器音画分析；此授权不代表已完成整片人工核看'
const isVersionedReview = (value: string) => value === 'las-video-v2' || value.startsWith('las-video-v2-')
const statuses: Record<string, string> = {
  not_reviewed: '待审核', approved: '已审核', revoked: '已撤销', stale: '资产已变化',
  not_authorized: '未授权机器分析', authorized: '已授权机器先分析',
  legacy_unbound: '历史审核（对象版本未绑定）',
  prepared: '待调度', submitting: '已认领', submitted: '已提交', running: '分析中',
  completed: '已完成', failed: '失败', unknown: '结果待核', cancelled: '已取消',
}

export function lasRunOrigin(recordMode?: string, reviewBound?: boolean, objectVersionBound?: boolean,
  machineAuthorizationBound?: boolean): string {
  if (recordMode === 'historical_backfill') return '历史回填'
  if (recordMode === 'live_pre_dispatch' && machineAuthorizationBound === true)
    return objectVersionBound === true ? '本项目机器先分析授权（未绑定人工核片）' : '机器授权对象版本待核'
  if (recordMode === 'live_pre_dispatch' && reviewBound === true)
    return objectVersionBound === true ? '本项目版本化审核后' : '历史审核（字节版本未证明）'
  return '审核来源待核'
}

export function lasRunEvidence(run: Pick<Run, 'status' | 'record_mode' | 'review_bound' |
  'machine_authorization_bound' | 'object_version_bound' | 'final_summary'>) {
  return {
    origin: lasRunOrigin(run.record_mode, run.review_bound, run.object_version_bound,
      run.machine_authorization_bound),
    warning: run.record_mode === 'historical_backfill'
      ? '本条是历史回填任务凭证，没有本项目整片审核绑定；不能用于证明 041 审核→提交→结果链路已跑通。'
      : run.machine_authorization_bound && run.object_version_bound
        ? '本任务没有绑定人工核看记录；机器摘要不得当作人工 Case。'
      : run.record_mode === 'live_pre_dispatch' && run.review_bound && !run.object_version_bound
        ? '本任务属于旧审核链路，未证明审核所见与供应商读取的是同一对象版本；仅作历史对账。'
        : lasRunOrigin(run.record_mode, run.review_bound, run.object_version_bound,
            run.machine_authorization_bound) !== '本项目版本化审核后'
          ? '审核绑定来源尚未核验，不能把本任务计入版本化整片分析验收。'
        : null,
    missingResult: run.status === 'completed' && !run.final_summary,
  }
}

type ObservationScene = { start: number | null; end: number | null; description: string }
type ObservationEvent = ObservationScene & { scenes: ObservationScene[] }
type Observation = { description: string; events: ObservationEvent[] }

function observationRange(value: unknown): { start: number | null; end: number | null } {
  if (!value || typeof value !== 'object') return { start: null, end: null }
  const range = value as Record<string, unknown>
  const start = typeof range.start === 'number' && Number.isFinite(range.start) && range.start >= 0
    ? range.start : null
  const end = typeof range.end === 'number' && Number.isFinite(range.end) && endIsValid(range.end, start)
    ? range.end : null
  return { start, end }
}

function endIsValid(end: number, start: number | null) {
  return end >= 0 && (start === null || end >= start)
}

export function parseLASMachineObservation(raw: string): Observation | null {
  let value: unknown
  try { value = JSON.parse(raw) } catch { return null }
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const source = value as Record<string, unknown>
  const description = typeof source.videoDescription === 'string' ? source.videoDescription.trim() : ''
  const events: ObservationEvent[] = Array.isArray(source.events) ? source.events
    .filter((item): item is Record<string, unknown> => !!item && typeof item === 'object' && !Array.isArray(item))
    .map(item => {
      const range = observationRange(item.timeRange)
      const scenes: ObservationScene[] = Array.isArray(item.actionsScenes) ? item.actionsScenes
        .filter((scene): scene is Record<string, unknown> => !!scene && typeof scene === 'object' && !Array.isArray(scene))
        .filter(scene => typeof scene.description === 'string' && !!scene.description.trim())
        .map(scene => ({ ...observationRange(scene.timeRange), description: (scene.description as string).trim() })) : []
      return { ...range, description: typeof item.description === 'string' ? item.description.trim() : '', scenes }
    }).filter(item => !!item.description) : []
  return description || events.length ? { description, events } : null
}

function timeLabel(seconds: number | null) {
  if (seconds === null) return '时间待核'
  const rounded = Math.floor(seconds)
  return `${Math.floor(rounded / 60)}:${String(rounded % 60).padStart(2, '0')}`
}

export function LASMachineObservation({ raw, onSeek }: { raw: string; onSeek?: (seconds: number) => void }) {
  const observation = parseLASMachineObservation(raw)
  if (!observation) return <div className="las-observation">
    <p className="muted">供应商结果无法安全结构化展示；请展开原始机器输出核查。</p>
    <details className="las-raw-output"><summary>查看原始机器输出</summary><pre>{raw}</pre></details>
  </div>
  return <div className="las-observation">
    <strong>机器整片观察</strong>
    {observation.description && <p>{observation.description}</p>}
    {!!observation.events.length && <div className="las-observation-events" aria-label="机器分段事件">
      {observation.events.map((event, index) => <article className="las-observation-event" key={index}>
        <div className="las-observation-time">{event.start !== null && onSeek
          ? <button type="button" className="las-observation-seek" onClick={() => onSeek(event.start!)}
              aria-label={`定位原片 ${timeLabel(event.start)}`}>{timeLabel(event.start)}</button>
          : timeLabel(event.start)} – {timeLabel(event.end)}</div>
        <p>{event.description}</p>
        {!!event.scenes.length && <details><summary>查看 {event.scenes.length} 个动作细节</summary>
          <ol>{event.scenes.map((scene, sceneIndex) => <li key={sceneIndex}>
            {scene.start !== null && onSeek
              ? <button type="button" className="las-observation-seek" onClick={() => onSeek(scene.start!)}
                  aria-label={`定位原片 ${timeLabel(scene.start)}`}>{timeLabel(scene.start)}</button>
              : timeLabel(scene.start)} – {timeLabel(scene.end)} {scene.description}
          </li>)}</ol>
        </details>}
      </article>)}
    </div>}
    <details className="las-raw-output"><summary>查看原始机器输出</summary><pre>{raw}</pre></details>
  </div>
}

export default function ProjectLASVideoReviewPanel({ projectId, videoId, canManage, onSeek }: {
  projectId: string
  videoId: string
  canManage: boolean
  onSeek?: (seconds: number) => void
}) {
  const [reviewVersion, setReviewVersion] = useState('las-video-v2')
  const [assets, setAssets] = useState<Asset[] | null>(null)
  const [runs, setRuns] = useState<Run[] | null>(null)
  const [selected, setSelected] = useState<Asset | null>(null)
  const [selectedReviewVersion, setSelectedReviewVersion] = useState('')
  const [playbackCandidate, setPlaybackCandidate] = useState<PlaybackCandidate | null>(null)
  const [url, setUrl] = useState('')
  const [played, setPlayed] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const [machineConfirmed, setMachineConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const pendingPrepare = useRef<ReturnType<typeof Modal.confirm> | null>(null)
  const contextKey = `${projectId}\0${videoId}`
  const contextRef = useRef(contextKey)
  const reviewVersionRef = useRef(reviewVersion)
  const statusSequenceRef = useRef(0)
  const playbackSequenceRef = useRef(0)
  contextRef.current = contextKey
  reviewVersionRef.current = reviewVersion
  const isCurrent = () => contextRef.current === contextKey

  useEffect(() => () => {
    pendingPrepare.current?.destroy()
    pendingPrepare.current = null
  }, [])

  useEffect(() => {
    pendingPrepare.current?.destroy(); pendingPrepare.current = null
    statusSequenceRef.current += 1
    playbackSequenceRef.current += 1
    setAssets(null); setRuns(null); setSelected(null); setUrl('')
    setSelectedReviewVersion(''); setPlaybackCandidate(null); setPlayed(false); setConfirmed(false)
    setMachineConfirmed(false); setBusy(false)
    setError(''); setNotice('')
    void loadStatus()
  }, [projectId, videoId])

  const loadStatus = async () => {
    const sequence = ++statusSequenceRef.current
    try {
      const result = await backend.project_las_video_review({
        project_id: projectId, video_id: videoId, action: 'status',
      }) as { runs: Run[] }
      if (isCurrent() && sequence === statusSequenceRef.current) setRuns(result.runs)
    } catch {
      if (isCurrent() && sequence === statusSequenceRef.current)
        setError('项目整片分析状态不可读取；请核对项目成员权限。')
    }
  }

  const refresh = async () => {
    if (!isCurrent() || !canManage || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$/.test(reviewVersion)) return
    pendingPrepare.current?.destroy(); pendingPrepare.current = null
    playbackSequenceRef.current += 1
    setBusy(true); setError(''); setNotice(''); setSelected(null); setUrl('')
    setSelectedReviewVersion(''); setPlaybackCandidate(null); setPlayed(false); setConfirmed(false)
    setMachineConfirmed(false)
    const version = reviewVersion
    try {
      const result = await backend.project_las_video_review({
        project_id: projectId, video_id: videoId, action: 'list', review_version: version,
      }) as { assets: Asset[] }
      if (isCurrent() && reviewVersionRef.current === version) {
        setAssets(result.assets)
        await loadStatus()
      }
    } catch {
      if (isCurrent() && reviewVersionRef.current === version)
        setError('整片视频列表不可用；请确认项目负责人权限与视频纳入状态。')
    } finally {
      if (isCurrent() && reviewVersionRef.current === version) setBusy(false)
    }
  }

  const playback = async (asset: Asset) => {
    if (!isCurrent()) return
    pendingPrepare.current?.destroy(); pendingPrepare.current = null
    const sequence = ++playbackSequenceRef.current
    setBusy(true); setError(''); setNotice(''); setSelected(null); setUrl('')
    setSelectedReviewVersion(''); setPlaybackCandidate(null); setPlayed(false); setConfirmed(false)
    setMachineConfirmed(false)
    const version = reviewVersion
    try {
      const result = await backend.project_las_video_review({
        project_id: projectId, video_id: videoId, asset_id: asset.asset_id,
        action: 'playback', review_version: version,
      }) as { playback_url: string; expires_in_seconds: number; object_version_id: string; manifest_fingerprint: string }
      if (typeof result.object_version_id !== 'string' || !result.object_version_id.trim() ||
          typeof result.manifest_fingerprint !== 'string' || !/^[a-f0-9]{64}$/i.test(result.manifest_fingerprint) ||
          result.expires_in_seconds !== 300 || new URL(result.playback_url).protocol !== 'https:') {
        throw new Error('video asset changed')
      }
      if (isCurrent() && reviewVersionRef.current === version && sequence === playbackSequenceRef.current) {
        setSelected(asset); setSelectedReviewVersion(version); setUrl(result.playback_url)
        setPlaybackCandidate({ assetId: asset.asset_id, reviewVersion: version,
          objectVersionId: result.object_version_id, manifestFingerprint: result.manifest_fingerprint })
      }
    } catch {
      if (isCurrent() && reviewVersionRef.current === version && sequence === playbackSequenceRef.current)
        setError('这份视频无法播放或资产已变化，请刷新后重试。')
    } finally {
      if (isCurrent() && reviewVersionRef.current === version && sequence === playbackSequenceRef.current) setBusy(false)
    }
  }

  const approve = async () => {
    if (!isCurrent() || !selected || !playbackCandidate || !played || !confirmed || !selectedReviewVersion ||
        selectedReviewVersion !== reviewVersion || playbackCandidate.reviewVersion !== reviewVersion ||
        playbackCandidate.assetId !== selected.asset_id || !isVersionedReview(reviewVersion) ||
        selected.review_status !== 'not_reviewed') return
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await backend.project_las_video_review({
        project_id: projectId, video_id: videoId, asset_id: selected.asset_id,
        action: 'approve', review_version: selectedReviewVersion,
        object_version_id: playbackCandidate.objectVersionId,
        expected_manifest_fingerprint: playbackCandidate.manifestFingerprint,
        consent_statement: consent,
      }) as { status: string; review_id: string }
      if (result.status !== 'approved') throw new Error('not saved')
      if (isCurrent() && reviewVersionRef.current === selectedReviewVersion) {
        setAssets(items => items?.map(item => item.asset_id === selected.asset_id
          ? { ...item, review_status: 'approved', review_id: result.review_id } : item) ?? null)
        setSelected({ ...selected, review_status: 'approved', review_id: result.review_id })
        setConfirmed(false)
        setNotice('本项目整片审核已保存；尚未发起付费分析。')
      }
    } catch {
      if (isCurrent() && reviewVersionRef.current === selectedReviewVersion)
        setError('审核未保存，资产或权限可能变化；请重新核对视频。')
    } finally {
      if (isCurrent() && reviewVersionRef.current === selectedReviewVersion) setBusy(false)
    }
  }

  const authorizeMachineFirst = async () => {
    if (!isCurrent() || !selected || !playbackCandidate || !machineConfirmed ||
        selectedReviewVersion !== reviewVersion || playbackCandidate.reviewVersion !== reviewVersion ||
        playbackCandidate.assetId !== selected.asset_id || !isVersionedReview(reviewVersion) ||
        !['not_authorized', 'revoked'].includes(selected.machine_authorization_status)) return
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await backend.project_las_video_review({
        project_id: projectId, video_id: videoId, asset_id: selected.asset_id,
        action: 'authorize_machine_first', review_version: selectedReviewVersion,
        object_version_id: playbackCandidate.objectVersionId,
        expected_manifest_fingerprint: playbackCandidate.manifestFingerprint,
        consent_statement: machineConsent,
      }) as { status: string; authorization_id: string; human_review_status: string }
      if (result.status !== 'authorized' || result.human_review_status !== 'not_asserted')
        throw new Error('authorization not saved')
      if (isCurrent() && reviewVersionRef.current === selectedReviewVersion) {
        setAssets(items => items?.map(item => item.asset_id === selected.asset_id
          ? { ...item, machine_authorization_status: 'authorized',
              machine_authorization_id: result.authorization_id } : item) ?? null)
        setSelected({ ...selected, machine_authorization_status: 'authorized',
          machine_authorization_id: result.authorization_id })
        setMachineConfirmed(false)
        setNotice('版本化视频的云端机器分析授权已保存；此授权不构成人工整片审核，尚未发起付费任务。')
      }
    } catch {
      if (isCurrent() && reviewVersionRef.current === selectedReviewVersion)
        setError('云端分析授权未确认保存；请刷新状态，不要重复提交。')
    } finally {
      if (isCurrent() && reviewVersionRef.current === selectedReviewVersion) setBusy(false)
    }
  }

  const prepareMachineFirst = (asset: Asset) => {
    if (!isCurrent() || !asset.machine_authorization_id || !isVersionedReview(reviewVersion)) return
    const version = reviewVersion
    pendingPrepare.current?.destroy()
    pendingPrepare.current = Modal.confirm({
      title: '启动一次火山 LAS 付费机器整片分析？',
      content: '这份视频已授权云端机器分析，但本任务不绑定人工完整核看。任务持久登记后等待 Worker 提交，可能产生费用；未知结果不会自动重提。',
      okText: '确认启动', cancelText: '取消',
      onCancel: () => { pendingPrepare.current = null },
      onOk: async () => {
        pendingPrepare.current = null
        if (!isCurrent() || reviewVersionRef.current !== version) return
        setBusy(true); setError(''); setNotice('')
        try {
          const result = await backend.project_las_video_review({
            project_id: projectId, video_id: videoId, action: 'prepare_machine_first',
            authorization_id: asset.machine_authorization_id,
          }) as { status: string; created: boolean; human_review_status: string }
          if (result.human_review_status !== 'not_asserted') throw new Error('review provenance changed')
          if (isCurrent() && reviewVersionRef.current === version) {
            setNotice(result.created
              ? '机器先分析请求已持久登记，等待调度；不等于供应商已接单，也不等于人工审核。'
              : `同一授权已有任务：${statuses[result.status] || result.status}；没有重复提交。`)
            await loadStatus()
          }
        } catch {
          if (isCurrent() && reviewVersionRef.current === version)
            setError('请求结果未确认，请刷新任务状态；勿盲目重复提交。')
        } finally {
          if (isCurrent() && reviewVersionRef.current === version) setBusy(false)
        }
      },
    })
  }

  const revokeMachineAuthorization = async (asset: Asset) => {
    if (!isCurrent() || !asset.machine_authorization_id) return
    pendingPrepare.current?.destroy(); pendingPrepare.current = null
    setBusy(true); setError(''); setNotice('')
    try {
      await backend.project_las_video_review({
        project_id: projectId, video_id: videoId, action: 'revoke_authorization',
        authorization_id: asset.machine_authorization_id,
      })
      if (isCurrent()) {
        setAssets(items => items?.map(item => item.asset_id === asset.asset_id
          ? { ...item, machine_authorization_status: 'revoked' } : item) ?? null)
        playbackSequenceRef.current += 1
        setSelected(null); setSelectedReviewVersion(''); setPlaybackCandidate(null)
        setUrl(''); setPlayed(false); setConfirmed(false); setMachineConfirmed(false)
        setNotice('机器分析授权已撤销；未提交的任务被取消，已提交任务可能仍会结算。')
      }
    } catch {
      if (isCurrent()) setError('撤销未完成，请刷新授权与任务状态。')
    } finally {
      if (isCurrent()) setBusy(false)
    }
  }

  const prepare = (asset: Asset) => {
    if (!isCurrent() || !asset.review_id || !isVersionedReview(reviewVersion)) return
    const version = reviewVersion
    pendingPrepare.current?.destroy()
    pendingPrepare.current = Modal.confirm({
      title: '启动一次火山 LAS 付费整片分析？',
      content: '仅分析这份已审核视频。保存后进入待调度队列；任务被 Worker 提交后可能产生费用。超时不会自动重提同一视频任务。',
      okText: '确认启动', cancelText: '取消',
      onCancel: () => { pendingPrepare.current = null },
      onOk: async () => {
        pendingPrepare.current = null
        if (!isCurrent() || reviewVersionRef.current !== version) return
        setBusy(true); setError(''); setNotice('')
        try {
          const result = await backend.project_las_video_review({
            project_id: projectId, video_id: videoId, action: 'prepare', review_id: asset.review_id,
          }) as { status: string; created: boolean }
          if (isCurrent() && reviewVersionRef.current === version) {
            setNotice(result.created
              ? '付费分析请求已持久登记，当前等待调度；不等于供应商已接单。'
              : `同一审核已有任务：${statuses[result.status] || result.status}；没有重复提交。`)
            await loadStatus()
          }
        } catch {
          if (isCurrent() && reviewVersionRef.current === version)
            setError('分析请求未确认保存，请刷新任务状态；勿盲目重复提交。')
        } finally {
          if (isCurrent() && reviewVersionRef.current === version) setBusy(false)
        }
      },
    })
  }

  const revoke = async (asset: Asset) => {
    if (!isCurrent() || !asset.review_id) return
    pendingPrepare.current?.destroy(); pendingPrepare.current = null
    setBusy(true); setError(''); setNotice('')
    try {
      await backend.project_las_video_review({
        project_id: projectId, video_id: videoId, action: 'revoke', review_id: asset.review_id,
      })
      if (isCurrent()) {
        setAssets(items => items?.map(item => item.asset_id === asset.asset_id
          ? { ...item, review_status: 'revoked' } : item) ?? null)
        playbackSequenceRef.current += 1
        setSelected(null); setSelectedReviewVersion(''); setPlaybackCandidate(null)
        setUrl(''); setPlayed(false); setConfirmed(false)
        setNotice('审核已撤销；未提交的任务不可再派发。已提交供应商的任务可能仍会结算。')
      }
    } catch {
      if (isCurrent()) setError('撤销未完成，请刷新审核和任务状态。')
    } finally {
      if (isCurrent()) setBusy(false)
    }
  }

  return <section className="detail-section las-review-section" aria-label="本项目整片音画分析">
    <div className="detail-section-head"><h4>本项目整片音画分析</h4>
      <Button loading={busy} onClick={canManage ? refresh : loadStatus}>刷新</Button></div>
    <p className="muted">火山 LAS 分析的是入库的完整视频和声音；结果是机器观察，不等于人工审片、因果结论或可直接采用的创作方案。供应商实扣须另行对账。</p>
    {error && <Alert type="error" showIcon message={error} />}
    {notice && <Alert type="success" showIcon message={notice} />}
    {canManage && <>
      <div className="las-review-version"><label htmlFor="project-las-review-version">审核版本</label>
        <Input id="project-las-review-version" value={reviewVersion} disabled={busy}
        onChange={event => {
          pendingPrepare.current?.destroy(); pendingPrepare.current = null
          playbackSequenceRef.current += 1
          reviewVersionRef.current = event.target.value
          setReviewVersion(event.target.value); setAssets(null); setSelected(null)
          setSelectedReviewVersion(''); setPlaybackCandidate(null)
          setUrl(''); setPlayed(false); setConfirmed(false); setMachineConfirmed(false)
        }} />
        <Button disabled={busy} onClick={refresh}>加载本版本视频</Button></div>
      {assets?.length === 0 && <p className="muted">本视频尚无可分析的入库整片。</p>}
      {assets?.map(asset => <div className="las-asset-card" key={asset.asset_id}>
        <div className="las-asset-head"><Tag>{!isVersionedReview(reviewVersion) && asset.review_status === 'approved'
          ? '历史 v1 审核' : statuses[asset.review_status] || asset.review_status}</Tag>
          {asset.machine_authorization_status === 'authorized' &&
            <Tag color="blue">已授权机器分析 · 未绑定人工核片</Tag>}
          <span>入库整片 · {asset.size_bytes < 1048576 ? '小于 1 MB' : `${(asset.size_bytes / 1048576).toFixed(1)} MB`}</span></div>
        <code>SHA-256 {asset.asset_sha256.slice(0, 12)}…</code>
        <div className="las-asset-actions"><Button disabled={busy} onClick={() => playback(asset)}>核看这份视频</Button>
        {asset.review_status === 'approved' && asset.review_id && <>
          {isVersionedReview(reviewVersion) &&
            <Button type="primary" disabled={busy} onClick={() => prepare(asset)}>启动一次分析</Button>}
          <Button danger disabled={busy} onClick={() => revoke(asset)}>撤销审核</Button>
        </>}</div>
        {['authorized', 'stale'].includes(asset.machine_authorization_status) && asset.machine_authorization_id &&
          <div className="las-asset-actions">
            {asset.machine_authorization_status === 'authorized' &&
              <Button type="primary" disabled={busy} onClick={() => prepareMachineFirst(asset)}>启动机器先分析</Button>}
            <Button danger disabled={busy} onClick={() => revokeMachineAuthorization(asset)}>撤销机器授权</Button>
          </div>}
      </div>)}
      {selected && url && <div className="las-review-playback">
        <video controls playsInline preload="metadata" src={url}
          aria-label="本项目待审核完整视频"
          onPlay={() => setPlayed(true)}
          onError={() => {
            playbackSequenceRef.current += 1
            setPlaybackCandidate(null); setPlayed(false); setConfirmed(false); setMachineConfirmed(false)
            setError('视频播放失败或链接过期，请刷新。')
          }} />
        <p className="muted">请人工核看完整画面和声音；系统只能确认发生过播放，不能代替你判断是否看完整片。播放链接有效期 5 分钟。</p>
        <code>资产 SHA {selected.asset_sha256.slice(0, 12)}… · 播放对象版本 {playbackCandidate?.objectVersionId || '待核'} · 本版本指纹 {playbackCandidate?.manifestFingerprint.slice(0, 12) || '待核'}…</code>
        {!isVersionedReview(reviewVersion) && <p className="muted">历史审核仅供核看；请切换到 v2 重新审核后发起新分析。</p>}
        {selected.review_status === 'not_reviewed' && isVersionedReview(reviewVersion) && <>
          <Checkbox checked={confirmed} disabled={!playbackCandidate || !played || busy}
            onChange={event => setConfirmed(event.target.checked)}>{consent}</Checkbox>{' '}
          <Button type="primary" disabled={!playbackCandidate || !played || !confirmed || busy} onClick={approve}>保存整片审核</Button>
        </>}
        {['not_authorized', 'revoked'].includes(selected.machine_authorization_status) && isVersionedReview(reviewVersion) && <div>
          <p className="muted">若希望先取得机器观察，可单独授权云端分析；这不表示你或系统已完整核看视频。</p>
          <Checkbox checked={machineConfirmed} disabled={!playbackCandidate || busy}
            onChange={event => setMachineConfirmed(event.target.checked)}>{machineConsent}</Checkbox>{' '}
          <Button disabled={!playbackCandidate || !machineConfirmed || busy}
            onClick={authorizeMachineFirst}>保存机器分析授权</Button>
        </div>}
      </div>}
    </>}
    {runs?.length === 0 && <p className="muted">本项目尚无这条视频的整片分析任务。</p>}
    {runs?.map(run => <details className="las-run-card" key={run.attempt_id}>
      <summary><Tag>{lasRunEvidence(run).origin} · {statuses[run.status] || run.status}</Tag>任务 {run.attempt_id.slice(0, 8)}
        {run.estimated_cost !== null ? ` · 估价 ${run.estimated_cost} ${run.cost_currency}` : ' · 费用待对账'}</summary>
      {lasRunEvidence(run).warning && <Alert type="info" showIcon message={lasRunEvidence(run).warning} />}
      {run.error_code && <Alert type="warning" message={`任务需核查：${run.error_code}`} />}
      {run.provider_task_ref && <p>供应商任务号：{run.provider_task_ref}</p>}
      {run.final_summary && <LASMachineObservation raw={run.final_summary} onSeek={onSeek} />}
      {lasRunEvidence(run).missingResult &&
        <p className="muted">本项目尚无可引用的机器整片摘要；任务完成状态与报价不能代替内容分析。</p>}
    </details>)}
  </section>
}
