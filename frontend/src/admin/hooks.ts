import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import type * as T from './types'

const useGet = <R,>(key: unknown[], path: string, opts: { refetchInterval?: number } = {}) =>
  useQuery({ queryKey: ['admin', ...key], queryFn: () => api<R>(path), ...opts })

const qs = (o: Record<string, string | number | boolean | undefined>) => {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(o)) if (v !== undefined && v !== '') p.set(k, String(v))
  const s = p.toString()
  return s ? `?${s}` : ''
}

export const useOverview = () => useGet<T.Overview>(['overview'], '/api/admin/stats/overview')
export const useDaily = (days = 14) => useGet<T.Daily>(['daily', days], `/api/admin/stats/daily${qs({ days })}`)
export const useExpiring = () => useGet<T.Expiring[]>(['expiring'], '/api/admin/accounts/expiring?days=14&limit=50')
export const useSyncStatus = () => useGet<T.SyncStatus>(['sync-status'], '/api/admin/syncs/status', { refetchInterval: 15_000 })
export const useRuns = (f: { status?: string; trigger?: string; user_id?: number }) => useGet<T.Run[]>(['runs', f], `/api/admin/syncs/runs${qs({ ...f, limit: 30 })}`)
export const useRunDetail = (id: number | null) =>
  useQuery({ queryKey: ['admin', 'run', id], queryFn: () => api<T.RunDetail>(`/api/admin/syncs/runs/${id}`), enabled: id !== null })
export const useEvents = (f: { level?: string; provider?: string }) => useGet<T.Event[]>(['events', f], `/api/admin/syncs/events${qs({ ...f, limit: 30 })}`)
export const useJobs = (status?: string) => useGet<T.Job[]>(['jobs', status], `/api/admin/syncs/jobs${qs({ status, limit: 30 })}`)
export const useUsers = (f: { q?: string; banned?: boolean }) => useGet<T.UserRow[]>(['users', f], `/api/admin/users${qs({ ...f, limit: 30 })}`)
export const useUser = (id: number) => useGet<T.UserDetail>(['user', id], `/api/admin/users/${id}`)
export const useUnmatched = (f: { q?: string; target?: string }) => useGet<T.Unmatched[]>(['unmatched', f], `/api/admin/titles/unmatched${qs({ ...f, limit: 50 })}`)
export const useConflicts = (status: 'open' | 'kept' | 'all') =>
  useGet<T.Conflicts>(['conflicts', status], `/api/admin/titles/conflicts${qs({ status })}`)
export const useProviderHealth = () => useGet<T.ProviderHealth[]>(['providers'], '/api/admin/providers')
export const useDataset = () => useGet<T.Dataset>(['dataset'], '/api/admin/dataset')
export const useAdminSite = () => useGet<T.AdminSite>(['site'], '/api/admin/site')

/** A write to the admin API; refreshes every admin query afterwards. */
export function useAct<V = void>(run: (v: V) => Promise<unknown>) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: run, onSuccess: () => qc.invalidateQueries({ queryKey: ['admin'] }) })
}

export const post = (path: string, json?: unknown) => api<unknown>(path, { method: 'POST', json })
export const put = (path: string, json: unknown) => api<unknown>(path, { method: 'PUT', json })
export const del = (path: string) => api<unknown>(path, { method: 'DELETE' })
