import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from './client'
import type { Me, MirrorResult, ProviderInfo, Site } from './types'

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
  useMeMutation((v: { id: number; sync_enabled?: boolean; prune_missing?: boolean }) =>
    api<Me>(`/api/accounts/${v.id}`, { method: 'PATCH', json: { sync_enabled: v.sync_enabled, prune_missing: v.prune_missing } }),
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
