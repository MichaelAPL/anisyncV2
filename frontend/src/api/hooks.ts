import { useQuery } from '@tanstack/react-query'
import { api, ApiError } from './client'
import type { Me, ProviderInfo, Site } from './types'

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
