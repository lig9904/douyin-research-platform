import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Button, Card, Descriptions, Form, Input, Modal, Select, Space, Spin, Tag, message } from 'antd'
import { backend } from './backend'

type ProfileKind = 'ip_narrative' | 'destination_experience' | 'activity_conversion' | 'other'
type ProfileStatus = 'draft' | 'approved' | 'superseded' | 'revoked'
type RightsStatus = 'unknown' | 'pending' | 'cleared' | 'restricted' | 'prohibited'
type ProfileAction = 'create_draft' | 'approve' | 'supersede' | 'revoke'
type ProfileSummary = Partial<Record<'target_audience' | 'shootable_scenes' | 'narrative_constraints' | 'forbidden_expressions' | 'current_facts', string[]>>
type SubjectProfile = {
  id: string
  profile_kind: ProfileKind
  version_no: number
  status: ProfileStatus
  summary: ProfileSummary
  rights_status: RightsStatus
  source_reference?: string | null
  source_digest?: string | null
  content_fingerprint?: string | null
  approved_at?: string | null
}
type Result = { profiles?: SubjectProfile[]; can_manage?: boolean; viewer_actor?: string; pending_action?: { status: 'saved' | 'unknown'; profile_id?: string } }
type FormValues = {
  profile_kind: ProfileKind
  target_audience?: string
  shootable_scenes?: string
  narrative_constraints?: string
  forbidden_expressions?: string
  current_facts?: string
  rights_status: RightsStatus
  source_reference: string
  source_digest: string
}

type PendingDraft = {
  action: 'create_draft'
  actor_id: string
  idempotency_key: string
  intent_hash: string
  created_at: number
}

const pendingDraftStoragePrefix = 'douyin-research:subject-profile:pending-draft:v1'
const pendingDraftMaxAgeMs = 24 * 60 * 60 * 1000

const kindLabels: Record<ProfileKind, string> = {
  ip_narrative: 'IP 叙事',
  destination_experience: '景区体验',
  activity_conversion: '活动转化',
  other: '其他业务档案',
}
const statusLabels: Record<ProfileStatus, string> = {
  draft: '待核准', approved: '已核准', superseded: '已替换', revoked: '已撤销',
}
const statusColors: Record<ProfileStatus, string> = {
  draft: 'gold', approved: 'green', superseded: 'default', revoked: 'red',
}
const rightsLabels: Record<RightsStatus, string> = {
  unknown: '尚未核验', pending: '待核验', cleared: '已确认可用', restricted: '受限使用', prohibited: '禁止使用',
}
const profileFields: Array<{ key: keyof ProfileSummary; title: string; hint: string }> = [
  { key: 'target_audience', title: '目标客群', hint: '例如 18–35 岁；未知请留空，不猜测' },
  { key: 'shootable_scenes', title: '当前可拍场景', hint: '仅写已确认可拍的场景摘要' },
  { key: 'narrative_constraints', title: '叙事 / 品牌边界', hint: '例如可表达的主题与不得擅自延展的设定' },
  { key: 'forbidden_expressions', title: '禁用表达', hint: '法律、品牌、角色或运营禁区' },
  { key: 'current_facts', title: '当前事实', hint: '只写有来源可核对的当前事实' },
]

function displayDate(value?: string | null) {
  if (!value) return '未核准'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN', { hour12: false })
}

function shortFingerprint(value?: string | null) {
  return value && /^[0-9a-f]{16,}$/i.test(value) ? `${value.slice(0, 12)}…${value.slice(-8)}` : '—'
}

function splitSummary(value?: string) {
  return (value || '').split(/\r?\n/)
    .map((item) => item.trim().replace(/\s+/g, ' '))
    .filter(Boolean)
}

function stableIntentValue(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value)
  if (Array.isArray(value)) return `[${value.map(stableIntentValue).join(',')}]`
  const record = value as Record<string, unknown>
  return `{${Object.keys(record).sort().map((key) => `${JSON.stringify(key)}:${stableIntentValue(record[key])}`).join(',')}}`
}

/** Pure, order-stable action intent key; exported for focused UI tests. */
export function subjectProfileIntentSignature(action: ProfileAction, payload: Record<string, unknown>) {
  return `${action}:${stableIntentValue(payload)}`
}

/** An opaque, stable browser-session key. It deliberately never contains form text. */
export function subjectProfilePendingDraftStorageKey(projectId: string, subjectId: string) {
  return `${pendingDraftStoragePrefix}:${encodeURIComponent(projectId)}:${encodeURIComponent(subjectId)}`
}

/**
 * Hash the action intent before it reaches sessionStorage. Form text is kept only
 * in component memory; after a refresh the user must re-enter it to retry.
 */
export async function subjectProfileIntentHash(action: ProfileAction, payload: Record<string, unknown>): Promise<string | null> {
  if (!globalThis.crypto?.subtle || typeof TextEncoder === 'undefined') return null
  try {
    const bytes = new TextEncoder().encode(subjectProfileIntentSignature(action, payload))
    const digest = await globalThis.crypto.subtle.digest('SHA-256', bytes)
    return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('')
  } catch { return null }
}

function pendingDraftStorage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.sessionStorage
  } catch {
    return null
  }
}

export function readPendingSubjectProfileDraft(projectId: string, subjectId: string): PendingDraft | null {
  const storage = pendingDraftStorage()
  if (!storage) return null
  const key = subjectProfilePendingDraftStorageKey(projectId, subjectId)
  try {
    const value = JSON.parse(storage.getItem(key) || 'null') as Partial<PendingDraft> | null
    if (!value || value.action !== 'create_draft' || typeof value.actor_id !== 'string' || !/^[^\s@]+@[^\s@]+$/.test(value.actor_id) || typeof value.idempotency_key !== 'string' || !/^[0-9a-f-]{16,}$/i.test(value.idempotency_key) || typeof value.intent_hash !== 'string' || !/^[0-9a-f]{64}$/i.test(value.intent_hash) || typeof value.created_at !== 'number' || !Number.isFinite(value.created_at) || value.created_at > Date.now() + 5 * 60 * 1000 || Date.now() - value.created_at > pendingDraftMaxAgeMs) {
      storage.removeItem(key)
      return null
    }
    return value as PendingDraft
  } catch {
    try { storage.removeItem(key) } catch { /* sessionStorage may be disabled */ }
    return null
  }
}

export function pendingSubjectProfileDraftBelongsToActor(pending: PendingDraft | null, actor: string) {
  return pending !== null && pending.actor_id === actor
}

export function savePendingSubjectProfileDraft(projectId: string, subjectId: string, pending: PendingDraft) {
  const storage = pendingDraftStorage()
  if (!storage) return false
  try {
    const key = subjectProfilePendingDraftStorageKey(projectId, subjectId)
    const value = JSON.stringify(pending)
    storage.setItem(key, value)
    return storage.getItem(key) === value
  } catch { return false /* sessionStorage may be full or disabled */ }
}

function clearPendingSubjectProfileDraft(projectId: string, subjectId: string) {
  try { pendingDraftStorage()?.removeItem(subjectProfilePendingDraftStorageKey(projectId, subjectId)) } catch { /* sessionStorage may be disabled */ }
}

export function isKnownActionError(error: unknown) {
  const text = error instanceof Error ? error.message : String(error)
  return /\b(?:ACTION_INVALID|PROFILE_(?:ID_INVALID|KIND_INVALID|NOT_DRAFT|NOT_APPROVED|NOT_REVOCABLE)|RIGHTS_STATUS_INVALID|SOURCE_(?:REFERENCE|DIGEST)_INVALID|SUMMARY_INVALID|RESEARCH_(?:ACTION_(?:IDEMPOTENCY_CONFLICT|IDENTITY_REQUIRED)|PROJECT_PROFILE_MANAGE_DENIED|SUBJECT_(?:ACCESS_DENIED|PROFILE_ACCESS_DENIED)|SUBJECT_PROFILE_ALREADY_APPROVED))\b/.test(text)
}

function draftPayload(values: FormValues): Record<string, unknown> {
  const summary = Object.fromEntries(profileFields
    .map(({ key }) => [key, splitSummary(values[key])])
    .filter(([, items]) => items.length)) as ProfileSummary
  return {
    profile_kind: values.profile_kind,
    summary,
    rights_status: values.rights_status,
    source_reference: values.source_reference.trim(),
    source_digest: values.source_digest.trim().toLowerCase(),
  }
}

function profilePayload(profile: SubjectProfile): Record<string, unknown> | null {
  if (!profile.source_reference || !profile.source_digest) return null
  return {
    profile_kind: profile.profile_kind,
    summary: profile.summary,
    rights_status: profile.rights_status,
    source_reference: profile.source_reference,
    source_digest: profile.source_digest,
  }
}

export default function SubjectProfilePanel({ projectId, subjectId, subjectName, subjectType }: {
  projectId: string
  subjectId: string
  subjectName?: string
  subjectType?: string
}) {
  const [profiles, setProfiles] = useState<SubjectProfile[]>([])
  const [canManage, setCanManage] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [modal, setModal] = useState<'create' | null>(null)
  const [form] = Form.useForm<FormValues>()
  const [toast, contextHolder] = message.useMessage()
  const requestVersion = useRef(0)
  const mounted = useRef(true)
  const scopeKey = `${projectId}:${subjectId}`
  const activeScope = useRef(scopeKey)
  activeScope.current = scopeKey
  const idempotencyKeys = useRef(new Map<string, string>())
  const pendingIntentByAction = useRef(new Map<ProfileAction, string>())
  const pendingDraftValues = useRef<FormValues | null>(null)
  const pendingDraft = useRef<PendingDraft | null>(null)
  const viewerActor = useRef('')
  const startingDraft = useRef(false)

  const clearPendingDraft = () => {
    pendingDraft.current = null
    pendingDraftValues.current = null
    clearPendingSubjectProfileDraft(projectId, subjectId)
  }

  const load = async (includeHistory = false) => {
    const requestedScope = scopeKey
    const requestedPending = includeHistory ? pendingDraft.current : null
    const version = ++requestVersion.current
    setLoading(true)
    setError('')
    try {
      const result = await backend.get_subject_profiles({
        project_id: projectId,
        subject_id: subjectId,
        ...(includeHistory ? { include_history: true } : {}),
        ...(requestedPending ? { pending_idempotency_key: requestedPending.idempotency_key } : {}),
      }) as Result
      if (version !== requestVersion.current || requestedScope !== activeScope.current) return
      const allowed = Boolean(result.can_manage)
      viewerActor.current = allowed ? (result.viewer_actor || '') : ''
      if (pendingDraft.current && !pendingSubjectProfileDraftBelongsToActor(pendingDraft.current, viewerActor.current)) clearPendingDraft()
      setCanManage(allowed)
      setProfiles(result.profiles || [])
      const pending = pendingDraft.current
      if (includeHistory && allowed && pending && pending.idempotency_key === requestedPending?.idempotency_key) {
        const receiptProfile = result.pending_action?.status === 'saved'
          ? (result.profiles || []).find((profile) => profile.id === result.pending_action?.profile_id)
          : null
        const payload = receiptProfile ? profilePayload(receiptProfile) : null
        const confirmed = payload !== null && await subjectProfileIntentHash('create_draft', payload) === pending.intent_hash
        if (version !== requestVersion.current || requestedScope !== activeScope.current) return
        if (confirmed) {
          const pendingDraftIntent = pendingIntentByAction.current.get('create_draft')
          if (pendingDraftIntent) idempotencyKeys.current.delete(pendingDraftIntent)
          pendingIntentByAction.current.delete('create_draft')
          clearPendingDraft()
          toast.success('已在档案历史确认上次草稿已保存。')
        } else if (result.pending_action?.status === 'saved') {
          setError('操作回执存在，但档案正文与本次待确认内容不一致；请停止重试并联系管理员核对。')
        }
      }
      // The first response is deliberately safe for ordinary readers. A
      // manager then makes a separate explicit request for draft/history.
      if (allowed && !includeHistory) void load(true)
    } catch (e) {
      if (version === requestVersion.current && requestedScope === activeScope.current) setError(e instanceof Error ? e.message : '主体档案加载失败')
    } finally {
      if (version === requestVersion.current && requestedScope === activeScope.current) setLoading(false)
    }
  }

  useEffect(() => {
    mounted.current = true
    setProfiles([])
    setCanManage(false)
    setModal(null)
    form.resetFields()
    idempotencyKeys.current.clear()
    pendingIntentByAction.current.clear()
    pendingDraftValues.current = null
    pendingDraft.current = readPendingSubjectProfileDraft(projectId, subjectId)
    viewerActor.current = ''
    void load()
    return () => { mounted.current = false; requestVersion.current += 1 }
  }, [projectId, subjectId])

  const approved = useMemo(() => profiles.find((item) => item.status === 'approved') || null, [profiles])
  const historical = useMemo(() => profiles.filter((item) => item.status !== 'approved'), [profiles])

  const openCreate = () => {
    if (pendingIntentByAction.current.has('create_draft') && pendingDraftValues.current) {
      form.setFieldsValue(pendingDraftValues.current)
      setModal('create')
      return
    }
    const defaultKind: ProfileKind = subjectType === 'ip' || subjectType === 'character'
      ? 'ip_narrative'
      : subjectType === 'destination'
        ? 'destination_experience'
        : subjectType === 'activity' || subjectType === 'product'
          ? 'activity_conversion'
          : 'other'
    form.setFieldsValue({
      profile_kind: defaultKind, rights_status: 'unknown', source_reference: '', source_digest: '',
      target_audience: '', shootable_scenes: '', narrative_constraints: '', forbidden_expressions: '', current_facts: '',
    })
    setModal('create')
  }

  const mutate = async (action: ProfileAction, fields: Record<string, unknown>, existingIdempotencyKey?: string) => {
    const intent = subjectProfileIntentSignature(action, fields)
    const previous = pendingIntentByAction.current.get(action)
    if (previous && previous !== intent) {
      const conflict = new Error('上一次同类操作结果尚未确认；请先刷新档案历史核对，不能直接用不同内容重试。')
      toast.warning(conflict.message)
      throw conflict
    }
    const idempotencyKey = existingIdempotencyKey || idempotencyKeys.current.get(intent) || globalThis.crypto?.randomUUID?.()
    if (!idempotencyKey) {
      const error = new Error('浏览器无法生成操作凭据；未发送保存请求。')
      toast.error(error.message)
      throw error
    }
    idempotencyKeys.current.set(intent, idempotencyKey)
    pendingIntentByAction.current.set(action, intent)
    setSaving(true)
    try {
      await backend.mutate_subject_profile({
        project_id: projectId,
        subject_id: subjectId,
        action,
        idempotency_key: idempotencyKey,
        ...fields,
      })
      idempotencyKeys.current.delete(intent)
      if (pendingIntentByAction.current.get(action) === intent) pendingIntentByAction.current.delete(action)
      if (action === 'create_draft') {
        clearPendingDraft()
      }
      if (mounted.current) {
        toast.success(action === 'create_draft' ? '档案草稿已保存，等待人工核准' : action === 'approve' ? '档案版本已核准' : action === 'supersede' ? '旧版本已标为替换' : '档案版本已撤销')
        setModal(null)
        await load(true)
      }
    } catch (e) {
      if (isKnownActionError(e)) {
        idempotencyKeys.current.delete(intent)
        if (pendingIntentByAction.current.get(action) === intent) pendingIntentByAction.current.delete(action)
        if (action === 'create_draft') {
          clearPendingDraft()
        }
        if (mounted.current) toast.error(e instanceof Error ? e.message : '主体档案保存失败')
      } else {
        if (mounted.current) {
          toast.error('提交结果尚未确认，已刷新档案历史；请核对后用相同内容重试。')
          void load(true)
        }
      }
      throw e
    } finally {
      if (mounted.current) setSaving(false)
    }
  }

  const createDraft = async (values: FormValues) => {
    if (startingDraft.current) return
    startingDraft.current = true
    try {
    const payload = draftPayload(values)
    const summary = payload.summary as ProfileSummary
    const groups = Object.values(summary) as string[][]
    if (!groups.length) {
      toast.error('至少填写一条可核验的业务档案摘要。')
      return
    }
    if (groups.some((items) => items.length > 50 || items.some((item) => item.length > 1000))) {
      toast.error('每个档案字段最多 50 条，且每条不超过 1000 个字符。')
      return
    }
    const intent = subjectProfileIntentSignature('create_draft', payload)
    const pending = pendingIntentByAction.current.get('create_draft')
    const intentHash = await subjectProfileIntentHash('create_draft', payload)
    if (!intentHash) {
      toast.error('当前浏览器无法生成待确认操作指纹；请在受支持的 HTTPS 浏览器中重试，未发送保存请求。')
      return
    }
    const recovered = pendingDraft.current
    if (!viewerActor.current) {
      toast.error('项目身份尚未确认；未发送保存请求。请刷新档案后重试。')
      return
    }
    if ((pending && pending !== intent) || (recovered && recovered.intent_hash !== intentHash)) {
      if (pendingDraftValues.current) form.setFieldsValue(pendingDraftValues.current)
      toast.warning('上一次草稿保存结果尚未确认；请先刷新历史核对，或按完全相同内容重试。刷新后不会保留正文。')
      return
    }
    const idempotencyKey = recovered?.idempotency_key || globalThis.crypto?.randomUUID?.()
    if (!idempotencyKey) {
      toast.error('浏览器无法生成操作凭据；未发送保存请求。')
      return
    }
    pendingDraftValues.current = { ...values }
    const nextPending: PendingDraft = { action: 'create_draft', actor_id: viewerActor.current, idempotency_key: idempotencyKey, intent_hash: intentHash, created_at: recovered?.created_at || Date.now() }
    if (!savePendingSubjectProfileDraft(projectId, subjectId, nextPending)) {
      toast.error('浏览器无法保存待确认操作凭据；未发送保存请求。请启用此页面的会话存储后重试。')
      return
    }
    pendingDraft.current = nextPending
    await mutate('create_draft', payload, idempotencyKey)
    } catch {
      // mutate already presents an actionable error and preserves an unconfirmed create.
    } finally {
      startingDraft.current = false
    }
  }

  const confirmLifecycle = (action: 'approve' | 'supersede' | 'revoke', profile: SubjectProfile) => {
    const actionName = action === 'approve' ? '核准' : action === 'supersede' ? '替换' : profile.status === 'draft' ? '撤销草稿' : '撤销'
    const description = action === 'approve'
      ? '核准只表示负责人确认了这份主体事实摘要及来源，不等于素材使用授权。权利状态未确认可用时不得据此开展创作或 L3；本操作不会自动触发 L3 或任何付费接口。'
      : action === 'supersede'
        ? '替换只关闭当前已核准版本。请按“新建草稿 → 将旧版标为已替换 → 人工核准新草稿”分别完成三步；它们不是原子操作，旧版关闭至新版核准前不会有可引用版本。'
        : profile.status === 'draft'
          ? '撤销后，这份待核准草稿不能再被核准或作为研究上下文。它从未获核准，也不代表素材或表达权利已获确认。'
          : '撤销后，该版本不得作为新的研究或分析上下文；已完成历史仍保留版本引用。'
    Modal.confirm({
      title: `${actionName}“${kindLabels[profile.profile_kind]} v${profile.version_no}”？`,
      content: description,
      okText: `确认${actionName}`,
      cancelText: '取消',
      okButtonProps: action === 'revoke' ? { danger: true } : undefined,
      onOk: () => mutate(action, { profile_id: profile.id }),
    })
  }

  return <Card className="subject-profile-panel" title="主体业务档案与核准版本" extra={<Space><Button size="small" onClick={() => void load(canManage)}>刷新</Button>{canManage ? <Button size="small" type="primary" onClick={openCreate}>新建档案草稿</Button> : null}</Space>}>
    {contextHolder}
    <Alert className="subject-profile-boundary" type="info" showIcon message="档案是研究的业务边界，不是素材库" description="仅记录可核验的结构化摘要与来源指纹。核准档案不等于获得素材使用许可；权利未确认可用时不得进入创作或 L3。不要上传完整原始脚本、私密素材、账号密码或 Cookie；此处不会触发 L3、媒体下载或任何付费接口。" />
    {error ? <Alert type="error" showIcon message="主体档案加载失败" description={error} /> : null}
    {loading ? <div className="subject-profile-loading"><Spin /></div> : <>
      {approved ? <section className="subject-profile-approved" aria-label="当前核准档案">
        <div className="subject-profile-head"><div><span>当前核准版本</span><h3>{subjectName || '当前研究主体'} · {kindLabels[approved.profile_kind]} v{approved.version_no}</h3></div><Space><Tag color="green">已核准</Tag><Tag color={approved.rights_status === 'cleared' ? 'green' : approved.rights_status === 'prohibited' ? 'red' : 'gold'}>{rightsLabels[approved.rights_status]}</Tag></Space></div>
        <Descriptions size="small" column={{ xs: 1, sm: 2 }}>
          {profileFields.map(({ key, title }) => approved.summary?.[key]?.length ? <Descriptions.Item key={key} label={title}><ul className="subject-profile-summary-list">{approved.summary[key]?.map((item, index) => <li key={`${key}-${index}`}>{item}</li>)}</ul></Descriptions.Item> : null)}
          <Descriptions.Item label="核准时间">{displayDate(approved.approved_at)}</Descriptions.Item>
          <Descriptions.Item label="内容指纹"><code>{shortFingerprint(approved.content_fingerprint)}</code></Descriptions.Item>
          {canManage ? <>
            <Descriptions.Item label="来源引用">{approved.source_reference || '—'}</Descriptions.Item>
            <Descriptions.Item label="来源摘要"><code>{shortFingerprint(approved.source_digest)}</code></Descriptions.Item>
          </> : null}
        </Descriptions>
        {canManage ? <div className="subject-profile-actions"><Button danger size="small" onClick={() => confirmLifecycle('revoke', approved)}>撤销当前版本</Button><Button size="small" onClick={() => confirmLifecycle('supersede', approved)}>标为已替换</Button></div> : null}
      </section> : <p className="subject-profile-empty">尚无已核准的业务档案。研究规则可以继续维护，但不能把缺失的角色设定、场景或素材权利补造为事实。</p>}

      {canManage && historical.length ? <section className="subject-profile-history" aria-label="档案历史">
        <h4>草稿与历史版本</h4>
        {historical.map((profile) => <article key={profile.id} className="subject-profile-history-row"><div><strong>{kindLabels[profile.profile_kind]} v{profile.version_no}</strong><span>{rightsLabels[profile.rights_status]} · 指纹 {shortFingerprint(profile.content_fingerprint)}</span></div><Space><Tag color={statusColors[profile.status]}>{statusLabels[profile.status]}</Tag>{profile.status === 'draft' ? <><Button size="small" type="primary" onClick={() => confirmLifecycle('approve', profile)}>人工核准</Button><Button size="small" danger onClick={() => confirmLifecycle('revoke', profile)}>撤销草稿</Button></> : null}</Space></article>)}
      </section> : null}

      {!canManage ? <p className="subject-profile-reader-note">你看到的是已核准的最小摘要；草稿、历史版本和来源引用仅对项目管理者开放。</p> : null}
    </>}

    <Modal open={modal === 'create'} title="新建主体档案草稿" width={760} onCancel={() => setModal(null)} onOk={() => form.submit()} okText="保存草稿" confirmLoading={saving} destroyOnClose>
      {pendingDraft.current || pendingIntentByAction.current.has('create_draft') ? <Alert type="warning" showIcon message="上一次草稿保存结果待核对" description="已保留幂等键，但不会写入浏览器正文。请先刷新档案历史确认是否已保存；若要重试，须按完全相同内容重新填写，不能另起一次创建。" /> : null}
      <Alert type="warning" showIcon message="只录入可核验摘要" description="不要粘贴完整剧本、未授权图片/视频、个人信息或凭据。来源摘要必须是对应资料的 SHA-256 指纹。" />
      <Form form={form} layout="vertical" onFinish={(values) => { void createDraft(values) }} className="subject-profile-form">
        <Form.Item name="profile_kind" label="档案类型" rules={[{ required: true }]}><Select options={Object.entries(kindLabels).map(([value, label]) => ({ value, label }))} /></Form.Item>
        {profileFields.map(({ key, title, hint }) => <Form.Item key={key} name={key} label={title} extra={`${hint}；一行一条，空行忽略，最多 50 条。`}><Input.TextArea autoSize={{ minRows: 2, maxRows: 6 }} /></Form.Item>)}
        <Form.Item name="rights_status" label="素材与表达权利状态" rules={[{ required: true }]}><Select options={Object.entries(rightsLabels).map(([value, label]) => ({ value, label }))} /></Form.Item>
        <Form.Item name="source_reference" label="来源引用" rules={[{ required: true, max: 512 }]} extra="填写受控资料编号或可核对链接；不填写账号密码、Cookie 或原始保密内容。"><Input maxLength={512} /></Form.Item>
        <Form.Item name="source_digest" label="来源 SHA-256" rules={[{ required: true, pattern: /^[0-9a-fA-F]{64}$/, message: '请输入 64 位十六进制 SHA-256' }]}><Input maxLength={64} /></Form.Item>
      </Form>
    </Modal>
  </Card>
}
