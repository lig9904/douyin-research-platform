import React, { useEffect, useState } from 'react'
import { Alert, Button, Modal, Spin, Tag } from 'antd'
import { backend } from './backend'
import { parseLASMachineObservation } from './src/components/ProjectLASVideoReviewPanel'
import {
  comparisonLibraryArgs, comparisonMetric, latestComparableRun, sameKnownAccount,
  type ComparableLASRun, type ComparableVideo,
} from './src/videoComparison'

type Evidence = { video: ComparableVideo; run: ComparableLASRun | null }

function dateLabel(value?: string | null) {
  if (!value) return '时间未知'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '时间待核' : date.toLocaleString('zh-CN')
}

function countLabel(value?: number | null) {
  return value == null ? '未知' : value.toLocaleString('zh-CN')
}

function timeLabel(value: number | null) {
  if (value == null) return '时间待核'
  const seconds = Math.floor(value)
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}

function ComparisonColumn({ evidence, onOpenVideo }: {
  evidence: Evidence
  onOpenVideo: (id: string) => void
}) {
  const { video, run } = evidence
  const metrics = comparisonMetric(video)
  const observation = run?.final_summary ? parseLASMachineObservation(run.final_summary) : null
  return <article className="project-compare-column">
    <div className="project-compare-title">
      <div><span className="project-compare-kicker">公开视频 · {video.platform_video_id}</span>
        <h3>{video.title}</h3><p>{video.account_name || '账号待核'} · 发布时间 {dateLabel(video.published_at)}</p></div>
      <Button size="small" onClick={() => onOpenVideo(video.id)}>回到视频详情</Button>
    </div>
    <dl className="project-compare-metrics">
      <div><dt>播放量</dt><dd>{metrics.playLabel}</dd></div>
      <div><dt>点赞 / 评论</dt><dd>{countLabel(video.like_count)} / {countLabel(video.comment_count)}</dd></div>
      <div><dt>分享</dt><dd>{metrics.shareLabel}</dd></div>
      <div><dt>互动/播放</dt><dd>{metrics.interactionRate}</dd></div>
    </dl>
    <p className="project-compare-provenance">合并指标采集：{dateLabel(video.metric_captured_at)}。字段可能来自不同请求时间；不含完播、曝光来源或年龄。</p>
    {run ? <>
      <div className="project-compare-evidence-title"><Tag color="blue">项目版本化机器观察</Tag>
        <small>任务 {run.attempt_id.slice(0, 8)} · {run.estimated_cost == null
          ? '费用待对账' : `预估 ${run.cost_currency || ''} ${run.estimated_cost}，非实扣`}</small></div>
      {observation ? <>
        <details className="project-compare-summary"><summary>查看机器整片概述</summary><p>{observation.description || '仅有分段观察'}</p></details>
        {observation.events.length ? <ol className="project-compare-events">
          {observation.events.map((event, index) => <li key={index}>
            <time>{timeLabel(event.start)}–{timeLabel(event.end)}</time><span>{event.description}</span>
          </li>)}
        </ol> : <p className="project-compare-empty">没有可解析的分段事件；请到视频详情核对原始机器输出。</p>}
      </> : <p className="project-compare-empty">机器结果未能安全结构化；请到视频详情核对原始输出。</p>}
    </> : <p className="project-compare-empty">尚无可用于项目对照的已完成版本化机器结果。历史回填或未绑定对象版本的结果不会充数。</p>}
  </article>
}

export default function ProjectVideoComparison({ projectId, videoIds, open, onClose, onOpenVideo }: {
  projectId: string
  videoIds: [string, string]
  open: boolean
  onClose: () => void
  onOpenVideo: (id: string) => void
}) {
  const [evidence, setEvidence] = useState<Evidence[] | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    if (!open) { setEvidence(null); setError(''); return }
    let cancelled = false
    setEvidence(null); setError('')
    void Promise.all(videoIds.map(async id => {
      const [library, status] = await Promise.all([
        backend.get_video_library(comparisonLibraryArgs(projectId, id)) as Promise<{ detail: ComparableVideo }>,
        backend.project_las_video_review({ project_id: projectId, video_id: id,
          action: 'status' }) as Promise<{ runs: ComparableLASRun[] }>,
      ])
      if (library.detail?.id !== id || library.detail.project_inclusion_status !== 'accepted')
        throw new Error('项目视频已变化或不再可用')
      return { video: library.detail, run: latestComparableRun(status.runs || []) }
    })).then(rows => {
      if (!cancelled) setEvidence(rows)
    }).catch(() => {
      if (!cancelled) setError('项目对照读取失败；可能是成员权限或视频纳入状态发生变化。请重新选择。')
    })
    return () => { cancelled = true }
  }, [open, projectId, videoIds[0], videoIds[1]])

  const sameAccount = evidence?.length === 2 ? sameKnownAccount(evidence[0].video, evidence[1].video) : null
  const sameDisplayedName = evidence?.length === 2 && !!evidence[0].video.account_name &&
    evidence[0].video.account_name === evidence[1].video.account_name
  return <Modal title="同项目双视频对照" open={open} onCancel={onClose} footer={null}
    width={1240} destroyOnHidden className="project-compare-modal">
    <p className="project-compare-intro">把两条已纳入视频的合并指标和项目专属机器观察放在一起核对。这里不生成因果结论，也不把机器结果升级成人工案例或行动卡。</p>
    {error ? <Alert type="error" showIcon message={error} /> : null}
    <Spin spinning={open && !evidence && !error}>
      {evidence?.length === 2 ? <>
        <Alert type={sameAccount === true ? 'info' : 'warning'} showIcon
          message={sameAccount === true ? '两条视频关联同一规范账号记录；仍需核对原帖归属，也不能据公开播放量推断同一批观众追更。'
            : sameAccount === false ? '两条视频关联不同规范账号记录：先核对原帖归属，不宜按播放量直接排序。'
              : sameDisplayedName ? '两条视频显示同名账号，但项目视图未提供账号 ID；先核对账号身份，再讨论连续作品。'
                : '账号身份尚未核实：先确认是否同一账号，再讨论连续作品。'} />
        <div className="project-compare-grid">
          {evidence.map(item => <ComparisonColumn key={item.video.id} evidence={item}
            onOpenVideo={onOpenVideo} />)}
        </div>
        <div className="project-compare-next">
          <strong>把对照转成行动前，还要核什么？</strong>
          <p>逐条核对开头问题、角色主动选择、可见后果和结尾回报；标记机器误识、镜头缺口、同账号发布时间及投流差异。仅在完整声画核看并保存项目 Case 后，才进入行动复盘；没有我方发布与观察窗数据时，结论保持待验证。</p>
        </div>
      </> : null}
    </Spin>
  </Modal>
}
