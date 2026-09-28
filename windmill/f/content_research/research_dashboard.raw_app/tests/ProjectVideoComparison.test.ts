import assert from 'node:assert/strict'
import { comparisonLibraryArgs, comparisonMetric, latestComparableRun, sameKnownAccount } from '../src/videoComparison'

const lookup = comparisonLibraryArgs('project-a', 'video-a')
assert.equal(lookup.project_id, 'project-a')
assert.equal(lookup.selected_video_id, 'video-a')
for (const field of ['days', 'research_level', 'priority_min', 'play_min', 'play_max',
  'follower_min', 'follower_max', 'page', 'page_size'] as const) {
  assert.equal(typeof lookup[field], 'number', `${field} must not be omitted`)
}
assert.equal(lookup.source_type, 'all')
assert.equal(lookup.status, 'all')
assert.equal(lookup.collected, 'all')

const base = {
  id: 'video-a', platform_video_id: '123', title: '角色片 A',
  account_id: 'same-account', play_count: 100, like_count: 5,
  comment_count: 2, share_count: 1,
}
assert.equal(comparisonMetric(base).interactionRate, '8.0%')
assert.equal(comparisonMetric({ ...base, play_count: 0 }).playLabel, '0 · 待核')
assert.equal(comparisonMetric({ ...base, play_count: 0 }).interactionRate, '— · 待核')
assert.equal(comparisonMetric({ ...base, share_source_conflict: true }).interactionRate, '— · 待核')
assert.equal(comparisonMetric({ ...base, share_source_conflict: true }).shareLabel, '来源冲突 · 待核')
assert.equal(sameKnownAccount(base, { ...base, id: 'video-b' }), true)
assert.equal(sameKnownAccount(base, { ...base, account_id: 'other' }), false)
assert.equal(sameKnownAccount(base, { ...base, account_id: null }), null)
assert.equal(sameKnownAccount({ ...base, account_id: null, project_account_ref: 'scope-a' },
  { ...base, id: 'video-b', account_id: null, project_account_ref: 'scope-a' }), true)
assert.equal(sameKnownAccount({ ...base, project_account_ref: 'scope-a' },
  { ...base, project_account_ref: 'scope-b' }), false)
assert.equal(sameKnownAccount({ ...base, project_account_ref: 'scope-a' },
  { ...base, project_account_ref: null }), null)

const trusted = {
  attempt_id: 'new', status: 'completed', record_mode: 'live_pre_dispatch',
  object_version_bound: true, machine_authorization_bound: true,
  final_summary: '{"videoDescription":"角色做出选择"}',
}
assert.equal(latestComparableRun([
  { ...trusted, attempt_id: 'historical', record_mode: 'historical_backfill' },
  { ...trusted, attempt_id: 'running', status: 'running' },
  trusted,
])?.attempt_id, 'new')
assert.equal(latestComparableRun([{ ...trusted, object_version_bound: false }]), null)
assert.equal(latestComparableRun([{ ...trusted, final_summary: '  ' }]), null)
assert.equal(latestComparableRun([{ ...trusted, machine_authorization_bound: false,
  review_bound: true }])?.attempt_id, 'new')
console.log('Project video comparison guardrails passed')
