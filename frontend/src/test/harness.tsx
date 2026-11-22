import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { vi } from 'vitest'
import { AppRoutes } from '../App'
import type { Account, Me, ProviderInfo, Site } from '../api/types'

export const PROVIDERS: ProviderInfo[] = [
  { id: 'anilist', name: 'AniList', monogram: 'AL', colors: {}, auth_kind: 'oauth_code', can_sign_in: true, paused: false, paused_reason: null, adult_help: null },
  { id: 'mal', name: 'MyAnimeList', monogram: 'MAL', colors: {}, auth_kind: 'oauth_code', can_sign_in: true, paused: false, paused_reason: null, adult_help: null },
  { id: 'kitsu', name: 'Kitsu', monogram: 'K', colors: {}, auth_kind: 'password', can_sign_in: false, paused: false, paused_reason: null, adult_help: 'In Kitsu, open Settings → Adult Content and allow adult titles' },
]

export const SITE: Site = { maintenance: false, maintenance_message: null, banner_text: null, banner_level: 'info', updated_at: '2026-01-01T00:00:00Z', donation_url: null }

export const account = (id: number, provider: string, over: Partial<Account> = {}): Account => ({
  id, provider, remote_username: `${provider}-user`, is_master: false, sync_enabled: true, prune_missing: false, last_synced_at: null,
  expires_at: null, needs_relink: false, expires_soon: false, avatar_url: null, hides_adult: false, skip_adult: false, ...over,
})

export const makeMe = (over: Partial<Me> = {}): Me => ({
  id: 1, display_name: 'Kaito', avatar_url: null, avatar_provider: null, theme: 'system', show_adult_covers: false, ignore_adult_titles: false, master_account_id: 1, role: 'user', permissions: [],
  next_sync_at: null, accounts: [account(1, 'anilist', { is_master: true }), account(2, 'mal')], ...over,
})

type Json = object | string | number | boolean | null
type Reply = Json | ((body: Record<string, unknown>, url: URL) => unknown)
export interface Call { method: string; path: string; body: unknown }

/**
 * Stub for `fetch`: routes are "METHOD /path" -> JSON (or a function of the body). Unmatched requests fail the test,
 * so a screen can't quietly call an endpoint the test doesn't know about. Returns the list of calls made.
 */
export function stubApi(routes: Record<string, Reply>, { me = makeMe() as Me | null } = {}) {
  const calls: Call[] = []
  const table: Record<string, Reply> = {
    'GET /api/me': me ?? { __status: 401 },
    'GET /api/providers': PROVIDERS,
    'GET /api/site': SITE,
    ...routes,
  }
  vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://localhost')
    const method = (init?.method ?? 'GET').toUpperCase()
    const body = init?.body ? JSON.parse(String(init.body)) : undefined
    calls.push({ method, path: url.pathname + url.search, body })
    const key = `${method} ${url.pathname}`
    if (!(key in table)) throw new Error(`Unexpected request: ${key}`)
    const reply = table[key]
    const value = typeof reply === 'function' ? await reply(body ?? {}, url) : reply
    const status = (value as { __status?: number } | null)?.__status
    if (status) return new Response(JSON.stringify({ detail: (value as { detail?: string }).detail ?? 'error' }), { status, headers: { 'content-type': 'application/json' } })
    return new Response(JSON.stringify(value), { status: 200, headers: { 'content-type': 'application/json' } })
  })
  return calls
}

export function renderRoute(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}><AppRoutes /></MemoryRouter>
    </QueryClientProvider>,
  )
}
