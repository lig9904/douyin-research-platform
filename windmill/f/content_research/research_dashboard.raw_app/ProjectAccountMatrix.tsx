import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Button, Select, Spin, Tag } from 'antd'
import { backend } from './backend'
import AppShell, { type ResearchView } from './AppShell'
import PlatformIcon from './src/components/PlatformIcon'
import type { ProjectScope } from './src/projectScope'
import './project-account-matrix.css'

type AccountRelation = {
  relation_id: string
  relation_type: string
  task_roles: string[]
  verification_status: string
  relation_effective_from?: string | null
  relation_effective_until?: string | null
  subject_id?: string | null
  subject_name?: string | null
  subject_type?: string | null
  account_id: string
  platform: string
  platform_account_id: string
  nickname?: string | null
  profile_url?: string | null
  bio?: string | null
  location_text?: string | null
  account_type?: string | null
  certification_type?: string | null
  first_seen_at?: string | null
  last_seen_at?: string | null
  included_public_video_count?: number | null
  last_included_at?: string | null
  follower_count?: number | null
  follower_captured_at?: string | null
}

type MatrixData = {
  project?: { name?: string | null; organization_name?: string | null }
  accounts?: AccountRelation[]
  has_more?: boolean
  next_cursor?: string | null
}

type Filters = {
  platform: string
  relationType: string
  taskRole: string
  subjectType: string
  accountType: string
}

const emptyFilters: Filters = {
  platform: 'all',
  relationType: 'all',
  taskRole: 'all',
  subjectType: 'all',
  accountType: 'all',
}

const relationLabels: Record<string, string> = {
  official: '官方账号',
  ip_character: 'IP 角色账号',
  employee_store: '员工/门店账号',
  managed_matrix: '运营矩阵账号',
  authorized_partner: '已授权合作方',
  unverified_partner: '待核验合作方',
  competitor: '竞品账号',
  media_reference: '媒体参考',
  ugc_reference: '用户内容参考',
  other: '其他项目关系',
}

const roleLabels: Record<string, string> = {
  publish_channel: '发布渠道',
  distribution_partner: '分发合作',
  benchmark_sample: '对标样本',
  comment_observer: '评论观察',
  conversion_entry: '转化入口',
  research_reference: '研究参考',
}

const subjectLabels: Record<string, string> = {
  destination: '目的地',
  ip: 'IP',
  character: '角色',
  product: '产品',
  activity: '活动',
  brand: '品牌',
  account: '账号主体',
  topic: '话题',
  other: '其他主体',
}

const platformLabels: Record<string, string> = {
  douyin: '抖音',
  kuaishou: '快手',
  wechat_channels: '微信视频号',
  xiaohongshu: '小红书',
  bilibili: 'B站',
  weibo: '微博',
}

function nameFor(value: string | null | undefined, labels: Record<string, string>, fallback = '未填写') {
  if (!value) return fallback
  return labels[value] || value
}

function formatCount(value?: number | null) {
  if (value === null || value === undefined) return '—'
  const n = Number(value)
  if (!Number.isFinite(n)) return '—'
  if (Math.abs(n) >= 100000000) return `${(n / 100000000).toFixed(1)}亿`
  if (Math.abs(n) >= 10000) return `${(n / 10000).toFixed(1)}万`
  return n.toLocaleString('zh-CN')
}

function formatDate(value?: string | null) {
  if (!value) return '长期有效'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' }).format(date)
}

function uniqueOptions(values: (string | null | undefined)[], labels: Record<string, string>) {
  return [...new Set(values.filter((value): value is string => Boolean(value)))].sort().map((value) => ({
    value,
    label: nameFor(value, labels),
  }))
}

export default function ProjectAccountMatrix({
  scope,
  onNavigate,
}: {
  scope: Extract<ProjectScope, { mode: 'project' }>
  onNavigate: (view: ResearchView) => void
}) {
  const [data, setData] = useState<MatrixData | null>(null)
  const [filters, setFilters] = useState<Filters>(emptyFilters)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState('')
  const [loadMoreError, setLoadMoreError] = useState('')
  const [refreshVersion, setRefreshVersion] = useState(0)
  const requestEpoch = useRef(0)

  useEffect(() => {
    const epoch = ++requestEpoch.current
    setData(null)
    setFilters(emptyFilters)
    setLoading(true)
    setLoadingMore(false)
    setError('')
    setLoadMoreError('')
    void (async () => {
      try {
        const response = await backend.get_project_accounts({ project_id: scope.projectId, limit: 100 }) as MatrixData
        if (requestEpoch.current !== epoch) return
        setData({ ...response, accounts: Array.isArray(response.accounts) ? response.accounts : [] })
      } catch (caught) {
        if (requestEpoch.current !== epoch) return
        setError(caught instanceof Error ? caught.message : '项目账号矩阵暂时不可用')
      } finally {
        if (requestEpoch.current === epoch) setLoading(false)
      }
    })()
    return () => { requestEpoch.current += 1 }
  }, [scope.projectId, refreshVersion])

  const accounts = data?.accounts || []
  const options = useMemo(() => ({
    platform: uniqueOptions(accounts.map((item) => item.platform), platformLabels),
    relationType: uniqueOptions(accounts.map((item) => item.relation_type), relationLabels),
    taskRole: uniqueOptions(accounts.flatMap((item) => Array.isArray(item.task_roles) ? item.task_roles : []), roleLabels),
    subjectType: uniqueOptions(accounts.map((item) => item.subject_type), subjectLabels),
    accountType: uniqueOptions(accounts.map((item) => item.account_type), {}),
  }), [accounts])

  const visibleRows = useMemo(() => accounts.filter((item) => (
    (filters.platform === 'all' || item.platform === filters.platform)
    && (filters.relationType === 'all' || item.relation_type === filters.relationType)
    && (filters.taskRole === 'all' || item.task_roles?.includes(filters.taskRole))
    && (filters.subjectType === 'all' || item.subject_type === filters.subjectType)
    && (filters.accountType === 'all' || item.account_type === filters.accountType)
  )), [accounts, filters])

  const projectName = data?.project?.name || scope.projectName
  const selectFilter = (key: keyof Filters, value: string) => setFilters((current) => ({ ...current, [key]: value }))

  async function loadMore() {
    if (loadingMore || !data?.has_more || !data.next_cursor) return
    const epoch = requestEpoch.current
    setLoadingMore(true)
    setLoadMoreError('')
    try {
      const response = await backend.get_project_accounts({
        project_id: scope.projectId,
        limit: 100,
        after_relation_id: data.next_cursor,
      }) as MatrixData
      if (requestEpoch.current !== epoch) return
      setData((current) => current ? {
        ...current,
        accounts: [...(current.accounts || []), ...(Array.isArray(response.accounts) ? response.accounts : [])]
          .filter((item, index, rows) => rows.findIndex((other) => other.relation_id === item.relation_id) === index),
        has_more: response.has_more,
        next_cursor: response.next_cursor,
      } : current)
    } catch {
      if (requestEpoch.current === epoch) setLoadMoreError('下一页加载失败，已加载的本项目账号仍可查看。')
    } finally {
      if (requestEpoch.current === epoch) setLoadingMore(false)
    }
  }

  return (
    <AppShell
      activeView="accounts"
      onNavigate={onNavigate}
      title="项目账号矩阵"
      subtitle={`只展示已核验、当前有效的项目关系 · ${projectName}`}
      mainClassName="project-account-matrix-main"
      actions={<Button loading={loading} onClick={() => setRefreshVersion((value) => value + 1)}>刷新</Button>}
    >
      <Alert
        className="project-account-matrix-boundary"
        type="info"
        showIcon
        message="这是项目关系视图，不是平台账号归属证明"
        description="平台资料来自公开账号事实；“官方、竞品、合作”等仅表示本项目已核验的研究关系。账号类型如企业号、创作者类型不能推断所有权；当前接口未返回私有授权能力，统一标记为“未核验/不推断”。"
      />

      {error ? <Alert type="error" showIcon message="项目账号矩阵加载失败" description="未显示缓存或全局账号数据。请稍后刷新，或确认当前项目访问权限。" /> : null}

      <section className="project-account-fact-strip" aria-label="数据边界说明">
        <div><span>平台事实</span><strong>账号公开资料与已采集指标</strong><small>不会表示账号属于谁</small></div>
        <div><span>项目关系</span><strong>已加载 {accounts.length} 条已核验且有效关系</strong><small>{data?.has_more ? '还有更多；可继续加载' : '同一账号可因不同主体或用途出现多行'}</small></div>
        <div><span>授权能力</span><strong>未核验 / 不推断</strong><small>本页不展示或推导私有后台权限</small></div>
      </section>

      <section className="project-account-filter-card card" aria-label="筛选项目账号关系">
        <label>平台<Select value={filters.platform} onChange={(value) => selectFilter('platform', value)} options={[{ value: 'all', label: '全部平台' }, ...options.platform]} /></label>
        <label>关系类型<Select value={filters.relationType} onChange={(value) => selectFilter('relationType', value)} options={[{ value: 'all', label: '全部关系' }, ...options.relationType]} /></label>
        <label>任务角色<Select value={filters.taskRole} onChange={(value) => selectFilter('taskRole', value)} options={[{ value: 'all', label: '全部角色' }, ...options.taskRole]} /></label>
        <label>主体类型<Select value={filters.subjectType} onChange={(value) => selectFilter('subjectType', value)} options={[{ value: 'all', label: '全部主体类型' }, ...options.subjectType]} /></label>
        <label>账号类型<Select value={filters.accountType} onChange={(value) => selectFilter('accountType', value)} options={[{ value: 'all', label: '全部账号类型' }, ...options.accountType]} /></label>
      </section>

      <section className="project-account-table-card card">
        <div className="project-account-table-head">
          <div><strong>当前项目账号关系</strong><span>已加载范围内筛选后 {visibleRows.length} / {accounts.length} 条</span></div>
          <span>只读 · 筛选仅作用于已加载行</span>
        </div>
        <Spin spinning={loading}>
          <div className="project-account-table-wrap">
            <table className="project-account-table">
              <thead><tr><th>平台账号事实</th><th>项目关系</th><th>任务用途</th><th>关联主体</th><th>采集覆盖</th><th>授权能力</th></tr></thead>
              <tbody>
                {visibleRows.map((item) => (
                  <tr key={item.relation_id}>
                    <td className="project-account-profile-cell">
                      <div className="project-account-profile"><PlatformIcon platform={item.platform} /><div><strong>{item.nickname || item.platform_account_id}</strong><small>{nameFor(item.platform, platformLabels)} · {item.platform_account_id}</small><span>{item.account_type ? `账号类型：${item.account_type}` : '账号类型未填写'}{item.certification_type ? ` · 认证：${item.certification_type}` : ''}</span></div></div>
                    </td>
                    <td><Tag color="blue">已核验</Tag><strong className="project-account-relation">{nameFor(item.relation_type, relationLabels)}</strong><small>有效至：{formatDate(item.relation_effective_until)}</small></td>
                    <td><div className="project-account-tags">{(item.task_roles || []).map((role) => <Tag key={role}>{nameFor(role, roleLabels)}</Tag>)}</div></td>
                    <td><strong>{item.subject_name || '未绑定项目主体'}</strong><small>{nameFor(item.subject_type, subjectLabels, '主体类型未填写')}</small></td>
                    <td><strong>{formatCount(item.included_public_video_count)} 条已纳入视频</strong><small>最近纳入：{item.last_included_at ? formatDate(item.last_included_at) : '无'}</small><small>粉丝快照：{formatCount(item.follower_count)} · {item.follower_captured_at ? formatDate(item.follower_captured_at) : '未采集'}</small></td>
                    <td><Tag>未核验 / 不推断</Tag><small>不把账号类型、关系类型当作后台权限</small></td>
                  </tr>
                ))}
                {!loading && visibleRows.length === 0 ? <tr><td className="project-account-empty" colSpan={6}>当前筛选没有已核验且有效的项目账号关系。</td></tr> : null}
              </tbody>
            </table>
          </div>
        </Spin>
        {loadMoreError ? <Alert type="warning" showIcon message={loadMoreError} /> : null}
        {data?.has_more ? <div className="project-account-load-more"><Button loading={loadingMore} onClick={() => void loadMore()}>继续加载本项目账号</Button></div> : null}
      </section>
    </AppShell>
  )
}
