import { useOutletContext } from 'react-router'
import { ApiError } from '../api/client'
import { useProviders } from '../api/hooks'
import type { Me } from '../api/types'

export const can = (me: Me, perm: string) => me.permissions.includes(perm)
export const useAdmin = () => useOutletContext<Me>()
export const errText = (e: unknown) => (e instanceof ApiError ? e.message : e ? "Couldn't reach AniSyncHub." : null)

export function useProviderName() {
  const { data } = useProviders()
  return (id: string | null | undefined) => (id ? data?.find((p) => p.id === id)?.name ?? id : '–')
}

export const level = (s: string) =>
  s === 'error' || s === 'failed' ? 'tag-err' : s === 'warning' ? 'tag-warn' : s === 'ok' || s === 'success' || s === 'done' ? 'tag-ok' : 'tag-neutral'
