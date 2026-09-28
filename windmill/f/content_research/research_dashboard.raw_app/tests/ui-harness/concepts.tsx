import React, { useState } from 'react'
import { createRoot } from 'react-dom/client'
import ProjectConcepts from '../../ProjectConcepts'
import { ProjectScopeProvider, type ProjectScope } from '../../src/projectScope'

declare global {
  interface Window {
    conceptCalls: Array<Record<string, unknown>>
    conceptMock: (args: Record<string, unknown>) => Promise<Record<string, unknown>>
  }
}

const PROJECT_A = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
const PROJECT_B = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
type Revision = Record<string, unknown> & { version_no: number }
const concepts: Record<string, Array<Record<string, unknown> & { revisions: Revision[] }>> = {
  [PROJECT_A]: [], [PROJECT_B]: [],
}
window.conceptCalls = []
window.conceptMock = async args => {
  window.conceptCalls.push(args)
  const project = String(args.project_id)
  const rows = concepts[project]
  if (!rows) throw new Error('unknown project')
  if (args.action === 'list') {
    if (project === PROJECT_B) await new Promise(resolve => setTimeout(resolve, 300))
    return {
    project_name: project === PROJECT_A ? '九九 IP 号' : '只读对照项目',
    role: project === PROJECT_A ? 'owner' : 'viewer',
    concepts: rows.map(row => ({ ...row, ...row.revisions.at(-1) })),
    boundary: '原创选题仅为本项目内部提案；待试片不是正式批准。',
    truncated: false,
    }
  }
  if (args.action === 'history') {
    const conceptId = String((args.payload as Record<string, unknown>).concept_id)
    const row = rows.find(item => item.id === conceptId)
    if (!row) throw new Error('not found')
    return { concept_id: conceptId, revisions: [...row.revisions].reverse(), has_more: false }
  }
  if (project !== PROJECT_A) throw new Error('read only')
  const payload = args.payload as Record<string, unknown>
  const now = new Date().toISOString()
  if (args.action === 'create') {
    const id = crypto.randomUUID()
    rows.push({ id, created_by: 'owner@example.com', created_at: now,
      revisions: [{ ...payload, version_no: 1, recorded_by: 'owner@example.com', recorded_at: now }] })
    return { concept_id: id, version_no: 1, status: payload.status }
  }
  if (args.action === 'revise') {
    const row = rows.find(item => item.id === payload.concept_id)
    if (!row || row.revisions.length !== payload.expected_version) throw new Error('version conflict')
    row.revisions.push({ ...payload, version_no: row.revisions.length + 1,
      recorded_by: 'owner@example.com', recorded_at: now })
    return { concept_id: row.id, version_no: row.revisions.length, status: payload.status }
  }
  throw new Error('unexpected action')
}

function Harness() {
  const [projectId, setProjectId] = useState(PROJECT_A)
  const scope: ProjectScope = { mode: 'project', projectId,
    projectName: projectId === PROJECT_A ? '九九 IP 号' : '只读对照项目' }
  return <>
    <button id="switch-project" onClick={() => setProjectId(projectId === PROJECT_A ? PROJECT_B : PROJECT_A)}>切换项目</button>
    <ProjectScopeProvider value={{ scope, projects: [
      { id: PROJECT_A, name: '九九 IP 号' },
      { id: PROJECT_B, name: '只读对照项目' },
    ], legacyAdmin: false, loading: false, error: '',
    chooseScope: next => next.mode === 'project' && setProjectId(next.projectId) }}>
      <ProjectConcepts scope={scope} onNavigate={() => {}} />
    </ProjectScopeProvider>
  </>
}
createRoot(document.getElementById('root')!).render(<Harness />)
