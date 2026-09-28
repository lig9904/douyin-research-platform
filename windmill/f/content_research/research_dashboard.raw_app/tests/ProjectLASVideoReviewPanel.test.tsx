import React from 'react'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { renderToStaticMarkup } from 'react-dom/server'
import ProjectLASVideoReviewPanel, { LASMachineObservation, lasRunEvidence, lasRunOrigin,
  parseLASMachineObservation } from '../src/components/ProjectLASVideoReviewPanel'

assert.equal(lasRunOrigin('historical_backfill', false), '历史回填')
assert.equal(lasRunOrigin('live_pre_dispatch', true, true), '本项目版本化审核后')
assert.equal(lasRunOrigin('live_pre_dispatch', true, false), '历史审核（字节版本未证明）')
assert.equal(lasRunOrigin('live_pre_dispatch', false, true, true), '本项目机器先分析授权（未绑定人工核片）')
assert.equal(lasRunOrigin('live_pre_dispatch', false), '审核来源待核')
assert.equal(lasRunOrigin(undefined, undefined), '审核来源待核')
const historical = lasRunEvidence({status: 'completed', record_mode: 'historical_backfill',
  review_bound: false, final_summary: null})
assert.equal(historical.origin, '历史回填')
assert.match(historical.warning || '', /不能用于证明 041/)
assert.equal(historical.missingResult, true)
const reviewed = lasRunEvidence({status: 'completed', record_mode: 'live_pre_dispatch',
  review_bound: true, object_version_bound: true, final_summary: '整片画面与声音的机器摘要'})
assert.equal(reviewed.origin, '本项目版本化审核后')
assert.equal(reviewed.warning, null)
assert.equal(reviewed.missingResult, false)
const unbound = lasRunEvidence({status: 'completed', record_mode: 'live_pre_dispatch',
  review_bound: true, object_version_bound: false, final_summary: '旧结果'})
assert.match(unbound.warning || '', /同一对象版本/)
const machineFirst = lasRunEvidence({status: 'completed', record_mode: 'live_pre_dispatch',
  review_bound: false, machine_authorization_bound: true, object_version_bound: true,
  final_summary: '机器摘要'})
assert.equal(machineFirst.origin, '本项目机器先分析授权（未绑定人工核片）')
assert.match(machineFirst.warning || '', /不得当作人工 Case/)

const machineOutput = JSON.stringify({videoDescription: '角色先遇到阻碍，再主动选择。', events: [
  {timeRange: {start: 0, end: 12.8}, description: '建立角色目标', actionsScenes: [
    {timeRange: {start: 2, end: 5.3}, description: '角色走近目标'}]},
  {timeRange: {start: 12.8, end: 71}, description: '选择带来后果'},
]})
assert.equal(parseLASMachineObservation(machineOutput)?.events.length, 2)
const observationMarkup = renderToStaticMarkup(<LASMachineObservation raw={machineOutput} />)
assert.match(observationMarkup, /机器整片观察/)
assert.match(observationMarkup, /0:00 – 0:12/)
assert.match(observationMarkup, /0:02 – 0:05/)
assert.match(observationMarkup, /查看 1 个动作细节/)
assert.match(observationMarkup, /查看原始机器输出/)
const seekMarkup = renderToStaticMarkup(<LASMachineObservation raw={machineOutput} onSeek={() => undefined} />)
assert.match(seekMarkup, /定位原片 0:00/)
assert.match(seekMarkup, /定位原片 0:02/)
assert.doesNotMatch(observationMarkup, /定位原片/)
assert.equal(parseLASMachineObservation('not-json'), null)
const fallbackMarkup = renderToStaticMarkup(<LASMachineObservation raw={'<untrusted>'} />)
assert.match(fallbackMarkup, /&lt;untrusted&gt;/)
assert.doesNotMatch(fallbackMarkup, /<untrusted>/)

const member = renderToStaticMarkup(<ProjectLASVideoReviewPanel
  projectId="test-project" videoId="test-video" canManage={false} />)
assert.match(member, /本项目整片音画分析/)
assert.match(member, /机器观察/)
assert.doesNotMatch(member, /启动一次分析|保存整片审核|<video/)

const manager = renderToStaticMarkup(<ProjectLASVideoReviewPanel
  projectId="test-project" videoId="test-video" canManage />)
assert.match(manager, /加载本版本视频/)
assert.match(manager, /las-video-v2/)
assert.doesNotMatch(manager, /启动一次分析|保存整片审核|<video/)

const component = readFileSync('src/components/ProjectLASVideoReviewPanel.tsx', 'utf8')
assert.match(component, /backend\.project_las_video_review\(/)
assert.match(component, /action: 'prepare'/)
assert.match(component, /action: 'prepare_machine_first'/)
assert.match(component, /此授权不代表已完成整片人工核看/)
assert.match(component, /Modal\.confirm/)
assert.match(component, /asset_manifest_fingerprint/)
assert.match(component, /object_version_id: playbackCandidate\.objectVersionId/)
assert.match(component, /expected_manifest_fingerprint: playbackCandidate\.manifestFingerprint/)
assert.match(component, /任务完成状态与报价不能代替内容分析/)
const library = readFileSync('VideoLibrary.tsx', 'utf8')
assert.match(library, /<ProjectLASVideoReviewPanel/)
console.log('Project LAS review initial rendering and explicit paid-action checks passed')
