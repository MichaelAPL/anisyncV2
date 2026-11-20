import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { isRunning } from '../format'
import { api, ApiError } from './client'
import type { EntryInput, ListEntry, Me, MirrorResult, ProviderInfo, SessionInfo, Site, SyncRun } from './types'

/** The signed-in user, or null when signed out (a 401 is an answer, not an error). */
export function useMe() {
  return useQuery({
    queryKey: ['me'],
    queryFn: async () => {
      try {
        return await api<Me>('/api/me')
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null
        throw e
      }
    },
    staleTime: 30_000,
  })
}

export const useProviders = () =>
  useQuery({ queryKey: ['providers'], queryFn: () => api<ProviderInfo[]>('/api/providers'), staleTime: 5 * 60_000 })

export const useSite = () =>
  useQuery({ queryKey: ['site'], queryFn: () => api<Site>('/api/site'), staleTime: 60_000, refetchInterval: 5 * 60_000 })

/** Mutations that answer with the updated `Me` put it straight into the cache. */
function useMeMutation<V>(fn: (v: V) => Promise<Me>) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: fn, onSuccess: (me) => qc.setQueryData(['me'], me) })
}

export const useSetMaster = () =>
  useMeMutation((accountId: number) => api<Me>('/api/me/master', { method: 'PUT', json: { account_id: accountId } }))

export const usePatchAccount = () =>
  useMeMutation((v: { id: number; sync_enabled?: boolean; prune_missing?: boolean; skip_adult?: boolean }) =>
    api<Me>(`/api/accounts/${v.id}`, {
      method: 'PATCH',
      json: { sync_enabled: v.sync_enabled, prune_missing: v.prune_missing, skip_adult: v.skip_adult },
    }),
  )

export function useLinkWithPassword() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { provider: string; username: string; password: string }) =>
      api<{ ok: boolean }>(`/auth/${v.provider}/password`, { method: 'POST', json: { username: v.username, password: v.password } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['me'] }),
  })
}

/** Dry run of the next sync: what would change on each mirror. Never cached, it must reflect the lists right now. */
export const useSyncPreview = () =>
  useQuery({
    queryKey: ['sync-preview'],
    queryFn: () => api<Record<string, MirrorResult>>('/api/sync/preview'),
    gcTime: 0,
    staleTime: 0,
    retry: false,
  })

export function useStartSync() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api<{ queued: boolean }>('/api/sync', { method: 'POST' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['me'] }),
  })
}

/** Recent runs. Refreshes every few seconds while one is running (or while `poll` is set). */
export const useRuns = (poll = false) =>
  useQuery({
    queryKey: ['runs'],
    queryFn: () => api<SyncRun[]>('/api/sync/runs'),
    refetchInterval: (q) => (poll || q.state.data?.some(isRunning) ? 3000 : false),
  })

export const useList = () => useQuery({ queryKey: ['list'], queryFn: () => api<ListEntry[]>('/api/list'), staleTime: 60_000 })

export function useSaveEntry() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { mediaId: string; entry: EntryInput }) =>
      api<{ ok: boolean; queued_mirrors: number }>(`/api/list/${v.mediaId}`, { method: 'PUT', json: v.entry }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['list'] }),
  })
}

export function useRemoveEntry() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (mediaId: string) => api<{ ok: boolean }>(`/api/list/${mediaId}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['list'] }),
  })
}

export function useRefreshList() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api<{ mode: string; changed: number }>('/api/list/refresh', { method: 'POST' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['list'] }),
  })
}

export const useSessions = () => useQuery({ queryKey: ['sessions'], queryFn: () => api<SessionInfo[]>('/api/sessions') })

export const useSaveProfile = () =>
  useMeMutation((v: { display_name?: string; theme?: Me['theme']; show_adult_covers?: boolean; ignore_adult_titles?: boolean }) =>
    api<Me>('/api/me', { method: 'PATCH', json: v }),
  )
export const useRecheckAdult = () => useMeMutation((id: number) => api<Me>(`/api/accounts/${id}/recheck-adult`, { method: 'POST' }))
export const useUseAvatar = () => useMeMutation((accountId: number) => api<Me>('/api/me/avatar', { method: 'PUT', json: { account_id: accountId } }))
export const useRemoveAvatar = () => useMeMutation(() => api<Me>('/api/me/avatar', { method: 'DELETE' }))
export const useUnlink = () => useMeMutation((id: number) => api<Me>(`/api/accounts/${id}`, { method: 'DELETE' }))

export function useSignOut() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (everywhere: boolean) => api<{ ok: boolean }>(everywhere ? '/auth/logout-all' : '/auth/logout', { method: 'POST' }),
    onSuccess: () => qc.clear(),
  })
}

export function useRevokeSession() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => api<{ ok: boolean }>(`/api/sessions/${id}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['sessions'] }),
  })
}

export function useDeleteAccount() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (confirm: string) => api<{ ok: boolean }>('/api/me/delete', { method: 'POST', json: { confirm } }),
    onSuccess: () => qc.clear(),
  })
}
