import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { isKnownActionError, pendingSubjectProfileDraftBelongsToActor, readPendingSubjectProfileDraft, savePendingSubjectProfileDraft, subjectProfileIntentHash, subjectProfilePendingDraftStorageKey } from '../SubjectProfilePanel'

class MemorySessionStorage {
  private readonly values = new Map<string, string>()
  getItem(key: string) { return this.values.get(key) || null }
  setItem(key: string, value: string) { this.values.set(key, value) }
  removeItem(key: string) { this.values.delete(key) }
}

const storage = new MemorySessionStorage()
;(globalThis as unknown as { window: { sessionStorage: MemorySessionStorage } }).window = { sessionStorage: storage }

const projectA = 'project-a'
const projectB = 'project-b'
const subjectA = 'subject-a'
const payload = {
  profile_kind: 'ip_narrative',
  summary: { current_facts: ['只应留在内存的业务正文'] },
  rights_status: 'unknown',
  source_reference: 'controlled-source-1',
  source_digest: 'a'.repeat(64),
}
void (async () => {
  assert.ok(readFileSync('ResearchBriefs.tsx', 'utf8').includes('key={`${projectId}:${selectedSubjectId}`}'))
  assert.equal(isKnownActionError(new Error('PROFILE_NOT_REVOCABLE')), true)
  assert.equal(isKnownActionError(new Error('RESEARCH_ACTION_IDENTITY_REQUIRED')), true)
  assert.equal(isKnownActionError(new Error('network timeout')), false)
  const hash = await subjectProfileIntentHash('create_draft', payload)
  assert.match(hash || '', /^[0-9a-f]{64}$/)
  const cryptoDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'crypto')
  Object.defineProperty(globalThis, 'crypto', {
    configurable: true,
    value: { subtle: { digest: async () => { throw new Error('digest unavailable') } } },
  })
  assert.equal(await subjectProfileIntentHash('create_draft', payload), null)
  if (cryptoDescriptor) Object.defineProperty(globalThis, 'crypto', cryptoDescriptor)
  else delete (globalThis as { crypto?: Crypto }).crypto
  assert.notEqual(subjectProfilePendingDraftStorageKey(projectA, subjectA), subjectProfilePendingDraftStorageKey(projectB, subjectA))

  const key = subjectProfilePendingDraftStorageKey(projectA, subjectA)
  const record = { action: 'create_draft', actor_id: 'owner@example.com', idempotency_key: '0f0d6ac0-9910-4b90-8f2c-f247bf9b38f5', intent_hash: hash, created_at: Date.now() }
  assert.equal(savePendingSubjectProfileDraft(projectA, subjectA, record), true)
  assert.deepEqual(readPendingSubjectProfileDraft(projectA, subjectA), record)
  assert.equal(pendingSubjectProfileDraftBelongsToActor(record, 'owner@example.com'), true)
  assert.equal(pendingSubjectProfileDraftBelongsToActor(record, 'other@example.com'), false)
  assert.doesNotMatch(storage.getItem(key) || '', /只应留在内存的业务正文|controlled-source-1/)

  const blocked = new MemorySessionStorage()
  blocked.setItem = () => { throw new Error('session storage blocked') }
  ;(globalThis as unknown as { window: { sessionStorage: MemorySessionStorage } }).window.sessionStorage = blocked
  assert.equal(savePendingSubjectProfileDraft(projectA, subjectA, record), false)
  ;(globalThis as unknown as { window: { sessionStorage: MemorySessionStorage } }).window.sessionStorage = storage

  storage.setItem(key, JSON.stringify({ ...record, created_at: Date.now() - 25 * 60 * 60 * 1000 }))
  assert.equal(readPendingSubjectProfileDraft(projectA, subjectA), null)
  assert.equal(storage.getItem(key), null)
  console.log('Subject profile pending draft storage assertions passed')
})()
