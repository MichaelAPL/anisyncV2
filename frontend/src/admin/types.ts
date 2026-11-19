export interface Requests { requests: number; rate_limited: number; errors: number; avg_ms: number }
export interface ProviderAccounts { provider: string; accounts: number; masters: number; needs_relink: number; expiring_soon: number }
export interface Overview {
  users_total: number; users_with_master: number; new_today: number; new_7d: number; active_7d: number; active_30d: number
  banned: number; providers: ProviderAccounts[]
}
export interface Day { day: string; new_users: number; runs_ok: number; runs_error: number; changes: number }
export interface Daily { days: Day[]; provider_requests: (Requests & { day: string; provider: string })[] }
export interface Expiring {
  account_id: number; user_id: number; display_name: string; provider: string; remote_username: string
  is_master: boolean; expires_at: string; state: string
}
export interface Queue { queue: string; todo: number; doing: number; failed: number }
export interface SyncStatus {
  queue_available: boolean; queues: Queue[]; stalled_jobs: number
  last_batch: { hour: string; runs: number; ok: number; error: number; changes: number } | null
  next_batch_at: string; users_in_next_batch: number; full_reads_overdue: number
}
export interface AdminSite {
  maintenance: boolean; maintenance_message: string | null; banner_text: string | null
  banner_level: 'info' | 'warning' | 'error' | string; scheduled_syncs_paused: boolean; updated_at: string; updated_by_name: string | null
}
export interface Run {
  id: number; user_id: number; display_name: string | null; trigger: string; status: string; started_at: string
  finished_at: string | null; changes: number; mirrors: number; errors: number; warnings: number
}
export interface Event {
  id: number; run_id: number; user_id: number | null; display_name: string | null; account_id: number | null; provider: string | null
  level: string; kind: string; message: string; media_ref: string | null; created_at: string
}
export interface RunDetail { run: Run; summary: Record<string, unknown>; events: Event[] }
export interface Job {
  id: number; queue: string; task: string; status: string; attempts: number; scheduled_at: string | null
  user_id: number | null; account_id: number | null; trigger: string | null
}
export interface UserRow {
  id: number; display_name: string; avatar_url: string | null; role: string; created_at: string; banned_at: string | null
  master_provider: string | null; providers: string[]; last_seen_at: string | null
}
export interface AdminAccount {
  id: number; provider: string; remote_username: string; is_master: boolean; sync_enabled: boolean; prune_missing: boolean
  expires_at: string | null; needs_relink: boolean; last_synced_at: string | null; last_full_read_at: string | null; entries: number
}
export interface AdminAction {
  id: number; actor_id: number | null; actor_name: string | null; action: string; note: string | null
  details: Record<string, unknown>; created_at: string
}
export interface UserDetail {
  id: number; display_name: string; avatar_url: string | null; avatar_provider: string | null; role: string; created_at: string
  banned_at: string | null; ban_reason: string | null; banned_by: number | null; banned_by_name: string | null
  sessions: number; last_seen_at: string | null; manual_syncs_7d: number
  accounts: AdminAccount[]; recent_runs: Run[]; actions: AdminAction[]
}
export interface Unmatched { source: string; source_id: string; target: string; title: string; users: number; first_seen_at: string; last_seen_at: string }
export interface ProviderHealth {
  id: string; name: string; paused: boolean; paused_reason: string | null; changed_at: string | null; accounts: number
  today: Requests; last_7d: Requests
}
export interface Dataset {
  source_url: string; media: number
  coverage: { provider: string; ids: number; seed: number; learned: number; manual: number }[]
  imports: { id: number; dataset: string; version: string | null; status: string; stats: Record<string, unknown>; error: string | null; started_at: string; finished_at: string | null }[]
}
