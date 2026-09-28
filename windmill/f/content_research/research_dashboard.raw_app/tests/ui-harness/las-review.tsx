import React, { useState } from 'react'
import { createRoot } from 'react-dom/client'
import ProjectLASVideoReviewPanel, { LASMachineObservation } from '../../src/components/ProjectLASVideoReviewPanel'
import ProjectVideoMediaPreview, { type VideoSeekRequest } from '../../src/components/ProjectVideoMediaPreview'
import '../../video-library.css'

declare global {
  interface Window {
    lasCalls: Array<Record<string, unknown>>
    lasMock: (args: Record<string, unknown>) => Promise<Record<string, unknown>>
    lasDelayOldStatus: boolean
    lasShowStatusByProject: boolean
    lasPlaybackMalformed: boolean
  }
}

const PROJECT_A = '33333333-3333-4333-8333-333333333333'
const PROJECT_B = '55555555-5555-4555-8555-555555555555'
const asset = {
  asset_id: '11111111-1111-4111-8111-111111111111',
  asset_sha256: 'a'.repeat(64), size_bytes: 1024,
  asset_manifest_fingerprint: 'b'.repeat(64),
  review_id: null, review_status: 'not_reviewed',
}
window.lasCalls = []
window.lasDelayOldStatus = false
window.lasShowStatusByProject = false
window.lasPlaybackMalformed = false
let approved = false
window.lasMock = async args => {
  window.lasCalls.push(args)
  switch (args.action) {
    case 'status': {
      if (window.lasDelayOldStatus && args.project_id === PROJECT_A)
        await new Promise(resolve => setTimeout(resolve, 400))
      if (!window.lasShowStatusByProject) return { runs: [] }
      return { runs: [{
        attempt_id: args.project_id === PROJECT_A
          ? 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
          : 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
        status: 'running', error_code: null, provider_task_ref: null,
        receipt_id: null, estimated_cost: null, cost_currency: null,
        final_summary: null, provenance: 'live',
      }] }
    }
    case 'list': return { assets: [{
      ...asset,
      review_id: approved
        ? '22222222-2222-4222-8222-222222222222' : null,
      review_status: approved
        ? 'approved' : 'not_reviewed',
    }] }
    case 'playback': return window.lasPlaybackMalformed ? {
      playback_url: 'https://example.test/las-review-fixture.mp4',
      expires_in_seconds: 300,
      manifest_fingerprint: 'c'.repeat(64),
    } : {
      playback_url: 'https://example.test/las-review-fixture.mp4',
      expires_in_seconds: 300,
      object_version_id: 'fixture-object-version-2',
      manifest_fingerprint: 'c'.repeat(64),
    }
    case 'approve':
      approved = true
      return { status: 'approved', review_id: '22222222-2222-4222-8222-222222222222' }
    case 'prepare': return { status: 'prepared', created: true }
    case 'revoke':
      approved = false
      return { status: 'revoked' }
    default: throw new Error(`Unexpected LAS action: ${args.action}`)
  }
}
function Harness() {
  const [projectId, setProjectId] = useState(PROJECT_A)
  const [seekRequest, setSeekRequest] = useState<VideoSeekRequest | null>(null)
  const videoId = '44444444-4444-4444-8444-444444444444'
  return <>
    <ProjectVideoMediaPreview projectId={projectId} videoId={videoId} seekRequest={seekRequest} />
    <LASMachineObservation raw={JSON.stringify({events: [
      {timeRange: {start: 1, end: 1.5}, description: '待核机器事件'},
      {timeRange: {start: 9, end: 10}, description: '超出时长的错误时间码'},
    ]})} onSeek={seconds => setSeekRequest(previous => ({projectId, videoId, seconds,
      sequence: (previous?.sequence ?? 0) + 1}))} />
    <button onClick={() => setProjectId(current => current === PROJECT_A ? PROJECT_B : PROJECT_A)}>
      切换项目
    </button>
    <ProjectLASVideoReviewPanel
      projectId={projectId}
      videoId={videoId}
      canManage
    />
  </>
}
createRoot(document.getElementById('root')!).render(<Harness />)
