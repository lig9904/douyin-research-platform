import { formatPlayInteractionRate } from './playInteractionRate'

export type ComparableVideo = {
  id: string
  platform_video_id: string
  title: string
  account_id?: string | null
  project_account_ref?: string | null
  account_name?: string | null
  duration_ms?: number | null
  published_at?: string | null
  metric_captured_at?: string | null
  play_count?: number | null
  like_count?: number | null
  comment_count?: number | null
  share_count?: number | null
  share_source_conflict?: boolean | null
  project_inclusion_status?: string | null
}

export type ComparableLASRun = {
  attempt_id: string
  status: string
  record_mode?: string | null
  review_bound?: boolean
  machine_authorization_bound?: boolean
  object_version_bound?: boolean
  final_summary?: string | null
  receipt_id?: string | null
  estimated_cost?: string | null
  cost_currency?: string | null
  created_at?: string | null
}

// Windmill's generated backend bridge sends omitted optional parameters as
// null. get_video_library casts several of them immediately, so the comparison
// must provide the full, non-private filter contract even for a detail lookup.
export function comparisonLibraryArgs(projectId: string, videoId: string) {
  return {
    project_id: projectId,
    selected_video_id: videoId,
    platform: 'all',
    days: 365,
    research_level: -1,
    source_type: 'all',
    priority_min: -1,
    status: 'all',
    play_min: -1,
    play_max: -1,
    follower_min: -1,
    follower_max: -1,
    collected: 'all',
    query: '',
    sort: 'published_desc',
    page: 1,
    page_size: 10,
  }
}

// Only a completed, project-scoped, exact-object-version run can support a
// machine-only comparison. Historical backfills never satisfy this contract.
export function latestComparableRun(runs: ComparableLASRun[]): ComparableLASRun | null {
  return runs.find(run =>
    run.status === 'completed' &&
    run.record_mode === 'live_pre_dispatch' &&
    run.object_version_bound === true &&
    (run.machine_authorization_bound === true || run.review_bound === true) &&
    typeof run.final_summary === 'string' && !!run.final_summary.trim()
  ) || null
}

export function comparisonMetric(video: ComparableVideo) {
  const inconsistentZero = video.play_count === 0 &&
    [video.like_count, video.comment_count, video.share_count].some(
      value => typeof value === 'number' && value > 0
    )
  return {
    playLabel: inconsistentZero ? '0 · 待核' :
      video.play_count == null ? '未知' : video.play_count.toLocaleString('zh-CN'),
    interactionRate: inconsistentZero ? '— · 待核' : formatPlayInteractionRate(video),
    shareLabel: video.share_source_conflict ? '来源冲突 · 待核' :
      video.share_count == null ? '未知' : video.share_count.toLocaleString('zh-CN'),
  }
}

export function sameKnownAccount(left: ComparableVideo, right: ComparableVideo): boolean | null {
  if (left.project_account_ref || right.project_account_ref) {
    if (!left.project_account_ref || !right.project_account_ref) return null
    return left.project_account_ref === right.project_account_ref
  }
  if (!left.account_id || !right.account_id) return null
  return left.account_id === right.account_id
}
