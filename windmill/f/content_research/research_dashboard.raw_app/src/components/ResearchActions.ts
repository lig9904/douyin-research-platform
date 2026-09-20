import { backend } from '../../backend'

export type ResearchCollection = {
  name: string
  video_count: number
  account_count: number
  signal_count: number
}

export type SavedResearchFilter = {
  name: string
  filters: Record<string, string | number | boolean | null>
  updated_at?: string | null
}

export type ResearchUserState = {
  collections: ResearchCollection[]
  saved_filters: SavedResearchFilter[]
  view_key: 'videos' | 'accounts' | 'hotspots'
  read_only: true
}

export type ResearchMutation = {
  action: 'set_monitoring' | 'add_to_collection' | 'remove_from_collection' | 'save_filter'
  asset_type?: 'video' | 'account' | 'signal'
  asset_ids?: string[]
  monitoring_status?: string
  monitoring_priority?: number | null
  collection_name?: string
  note?: string
  view_key?: 'videos' | 'accounts' | 'hotspots'
  filter_name?: string
  filters_json?: string
}

function idempotencyKey() {
  if (!globalThis.crypto?.randomUUID) {
    throw new Error('当前浏览器不支持安全幂等键，请升级浏览器后重试。')
  }
  return globalThis.crypto.randomUUID()
}

export async function getResearchUserState(
  viewKey: ResearchUserState['view_key'],
): Promise<ResearchUserState> {
  return (await backend.get_research_user_state({ view_key: viewKey })) as ResearchUserState
}

export async function mutateResearchState(input: ResearchMutation) {
  return backend.mutate_research_state({
    action: input.action,
    idempotency_key: idempotencyKey(),
    asset_type: input.asset_type || '',
    asset_ids: input.asset_ids || [],
    monitoring_status: input.monitoring_status || '',
    monitoring_priority: input.monitoring_priority ?? null,
    collection_name: input.collection_name || '',
    note: input.note || '',
    view_key: input.view_key || '',
    filter_name: input.filter_name || '',
    filters_json: input.filters_json || '{}',
  })
}
