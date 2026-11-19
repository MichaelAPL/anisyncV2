export interface Account {
  id: number
  provider: string
  remote_username: string
  is_master: boolean
  sync_enabled: boolean
  prune_missing: boolean
  last_synced_at: string | null
  expires_at: string | null
  needs_relink: boolean
  expires_soon: boolean
  avatar_url: string | null
}

export interface Me {
  id: number
  display_name: string
  avatar_url: string | null
  avatar_provider: string | null
  theme: 'system' | 'light' | 'dark'
  master_account_id: number | null
  role: string
  permissions: string[]
  next_sync_at: string | null
  accounts: Account[]
}

export interface ProviderInfo {
  id: string
  name: string
  monogram: string
  colors: Record<string, string>
  auth_kind: string
  can_sign_in: boolean
  paused: boolean
  paused_reason: string | null
}

export interface Site {
  maintenance: boolean
  maintenance_message: string | null
  banner_text: string | null
  banner_level: string
  updated_at: string
}

export interface Change {
  op: 'create' | 'update' | 'delete'
  media_id: string
  fields: string[]
  title: string
}

export interface MirrorResult {
  provider: string
  username: string
  create: number
  update: number
  delete: number
  unmapped: number
  unmapped_titles: string[]
  changes?: Change[]
  errors?: string[]
  error?: string
  reauth_required?: boolean
}

export interface SyncRun {
  id: number
  trigger: string
  status: string
  summary: Record<string, MirrorResult>
  started_at: string
  finished_at: string | null
  planned: number
  processed: number
}

export type Status = 'watching' | 'completed' | 'on_hold' | 'dropped' | 'planning'

export interface ListEntry {
  media_id: string
  status: Status
  progress: number
  score: number | null // 0-100
  rewatching: boolean
  started_at: string | null
  finished_at: string | null
  updated_at: string | null
  title: string
  total_episodes: number | null
  image_url: string | null
}

export interface EntryInput {
  status: Status
  progress: number
  score: number | null
  rewatching: boolean
  started_at: string | null
  finished_at: string | null
}

export interface SessionInfo {
  id: number
  created_at: string
  last_seen_at: string
  user_agent: string
  current: boolean
}
