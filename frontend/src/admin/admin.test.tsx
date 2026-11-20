import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { makeMe, renderRoute, stubApi } from '../test/harness'

const admin = makeMe({ role: 'admin', permissions: ['stats.read', 'syncs.read', 'syncs.control', 'syncs.retry', 'users.read', 'users.ban', 'users.delete', 'users.manage_tokens', 'roles.manage', 'titles.link', 'providers.manage', 'site.manage'] })
const overview = { users_total: 12, users_with_master: 10, new_today: 1, new_7d: 3, active_7d: 7, active_30d: 9, banned: 2, providers: [{ provider: 'mal', accounts: 8, masters: 5, needs_relink: 1, expiring_soon: 0 }] }
const dashboardStubs = { 'GET /api/sync/runs': [] }

describe('admin area', () => {
  it('sends ordinary users back to the dashboard', async () => {
    localStorage.setItem('anisync-setup-done', '1')
    stubApi(dashboardStubs)
    renderRoute('/admin')
    expect(await screen.findByRole('heading', { name: 'Dashboard' })).toBeInTheDocument()
  })

  it('shows the overview to an admin, with only the sections their permissions allow', async () => {
    const limited = makeMe({ role: 'moderator', permissions: ['stats.read', 'users.read'] })
    stubApi({ 'GET /api/admin/stats/overview': overview, 'GET /api/admin/stats/daily': { days: [], provider_requests: [] }, 'GET /api/admin/accounts/expiring': [] }, { me: limited })
    renderRoute('/admin')
    expect(await screen.findByText('users')).toBeInTheDocument()
    expect(screen.getByText('12')).toBeInTheDocument()
    const nav = screen.getAllByRole('navigation', { name: /^admin$/i })[0]
    expect(nav).toHaveTextContent('Overview')
    expect(nav).toHaveTextContent('Users')
    expect(nav).not.toHaveTextContent('Site')
    expect(nav).not.toHaveTextContent('Providers')
  })

  it('bans a user only with a reason, and sends it', async () => {
    const detail = { id: 7, display_name: 'Sam', avatar_url: null, avatar_provider: null, role: 'user', created_at: new Date().toISOString(), banned_at: null, ban_reason: null, banned_by: null, banned_by_name: null, sessions: 1, last_seen_at: null, manual_syncs_7d: 0, accounts: [], recent_runs: [], actions: [] }
    const calls = stubApi({ 'GET /api/admin/users/7': detail, 'POST /api/admin/users/7/ban': {} }, { me: admin })
    renderRoute('/admin/users/7')
    const user = userEvent.setup()
    const ban = await screen.findByRole('button', { name: /ban user/i })
    expect(ban).toBeDisabled()
    await user.type(screen.getByLabelText(/reason/i), 'spam')
    await user.click(ban)
    await waitFor(() => expect(calls.find((c) => c.path === '/api/admin/users/7/ban')?.body).toEqual({ reason: 'spam' }))
  })

  it('needs the exact user id to delete a user', async () => {
    const detail = { id: 7, display_name: 'Sam', avatar_url: null, avatar_provider: null, role: 'user', created_at: new Date().toISOString(), banned_at: null, ban_reason: null, banned_by: null, banned_by_name: null, sessions: 1, last_seen_at: null, manual_syncs_7d: 0, accounts: [], recent_runs: [], actions: [] }
    const calls = stubApi({ 'GET /api/admin/users/7': detail, 'DELETE /api/admin/users/7': { ok: true }, 'GET /api/admin/users': [] }, { me: admin })
    renderRoute('/admin/users/7')
    const user = userEvent.setup()
    const del = await screen.findByRole('button', { name: /^delete user$/i })
    expect(del).toBeDisabled()
    await user.type(screen.getByLabelText(/type the user id/i), '8')
    expect(del).toBeDisabled()
    await user.clear(screen.getByLabelText(/type the user id/i))
    await user.type(screen.getByLabelText(/type the user id/i), '7')
    await user.click(del)
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE' && c.path === '/api/admin/users/7?confirm=7')).toBe(true))
  })

  it('saves the banner and maintenance settings', async () => {
    const site = { maintenance: false, maintenance_message: null, banner_text: null, banner_level: 'info', scheduled_syncs_paused: false, updated_at: 'a', updated_by_name: null }
    const calls = stubApi({ 'GET /api/admin/site': site, 'PUT /api/admin/site': site }, { me: admin })
    renderRoute('/admin/site')
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Text'), 'AniList is slow today')
    await user.selectOptions(screen.getByLabelText('Level'), 'warning')
    await user.click(screen.getByRole('switch', { name: /maintenance mode/i }))
    await user.click(screen.getByRole('button', { name: /^save$/i }))
    await waitFor(() => expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({ maintenance: true, maintenance_message: null, banner_text: 'AniList is slow today', banner_level: 'warning' }))
  })

  it('pauses a provider with a reason', async () => {
    const health = [{ id: 'mal', name: 'MyAnimeList', paused: false, paused_reason: null, changed_at: null, accounts: 3, today: { requests: 5, rate_limited: 0, errors: 0, avg_ms: 100 }, last_7d: { requests: 50, rate_limited: 0, errors: 1, avg_ms: 100 } }]
    const calls = stubApi({ 'GET /api/admin/providers': health, 'GET /api/admin/dataset': { source_url: 'u', media: 1, coverage: [], imports: [] }, 'PUT /api/admin/providers/mal': health[0] }, { me: admin })
    renderRoute('/admin/providers')
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText(/reason shown to users/i), 'outage')
    await user.click(screen.getByRole('button', { name: /pause provider/i }))
    await waitFor(() => expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({ paused: true, reason: 'outage' }))
  })
})

describe('ID conflicts', () => {
  const conflict = (over: object = {}) => ({
    id: 7, kind: 'already_has', detail: 'media 5 already has kitsu ID 1; ignoring 2', ids: { kitsu: '2', mal: '10' },
    links: { kitsu: 'https://kitsu.app/anime/2', mal: 'https://myanimelist.net/anime/10' }, titles: ['Natsume Yuujinchou Shichi'],
    seen: 3, first_seen_at: '2026-11-19T00:00:00Z', last_seen_at: new Date().toISOString(), status: 'open', note: null, resolved_at: null, ...over,
  })
  const stubs = (items = [conflict()], extra: Record<string, unknown> = {}) => ({
    'GET /api/admin/titles/unmatched': [],
    'GET /api/admin/titles/conflicts': { counts: { open: items.length, kept: 0 }, items },
    ...extra,
  })

  it('lists what disagrees with the dataset, with links to check each claim', async () => {
    stubApi(stubs(), { me: admin })
    renderRoute('/admin/titles?tab=conflicts')
    expect(await screen.findByText('Natsume Yuujinchou Shichi')).toBeInTheDocument()
    expect(screen.getByText(/seen 3 times/i)).toBeInTheDocument()
    const link = screen.getByRole('link', { name: /kitsu 2/i })
    expect(link).toHaveAttribute('href', 'https://kitsu.app/anime/2')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'))
    expect(screen.getByRole('link', { name: /download report/i })).toHaveAttribute('href', '/api/admin/titles/conflicts/report.csv?status=open')
  })

  it('only links to https pages', async () => {
    stubApi(stubs([conflict({ links: { kitsu: 'javascript:alert(1)', mal: 'http://myanimelist.net/anime/10' } })]), { me: admin })
    renderRoute('/admin/titles?tab=conflicts')
    await screen.findByText('Natsume Yuujinchou Shichi')
    expect(screen.queryByRole('link', { name: /kitsu 2/i })).toBeNull()
    expect(screen.queryByRole('link', { name: /myanimelist 10/i })).toBeNull()
    expect(screen.getByText(/kitsu 2/i)).toBeInTheDocument()
  })

  it('keeps the dataset answer with a note, and can reopen it', async () => {
    const calls = stubApi(stubs([conflict()], { 'PUT /api/admin/titles/conflicts/7': conflict({ status: 'kept' }) }), { me: admin })
    renderRoute('/admin/titles?tab=conflicts')
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Note'), 'kitsu has the wrong anilist id')
    await user.click(screen.getByRole('button', { name: /keep the dataset's answer/i }))
    await waitFor(() => expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({ status: 'kept', note: 'kitsu has the wrong anilist id' }))
  })

  it('offers to reopen a kept conflict and filters by status', async () => {
    const calls = stubApi(
      stubs([conflict({ status: 'kept', note: 'fine' })], { 'PUT /api/admin/titles/conflicts/7': conflict({ status: 'open' }) }),
      { me: admin },
    )
    renderRoute('/admin/titles?tab=conflicts')
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /kept 0/i }))
    await waitFor(() => expect(calls.some((c) => c.path === '/api/admin/titles/conflicts?status=kept')).toBe(true))
    expect(screen.getByRole('link', { name: /download report/i })).toHaveAttribute('href', expect.stringContaining('status=kept'))
    await user.click(await screen.findByRole('button', { name: /reopen/i }))
    await waitFor(() => expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({ status: 'open', note: 'fine' }))
  })

  it('is not shown to an admin who may not link titles', async () => {
    const limited = makeMe({ role: 'moderator', permissions: ['users.read', 'titles.link'].filter((p) => p !== 'titles.link') })
    const calls = stubApi({ 'GET /api/admin/titles/unmatched': [] }, { me: limited })
    renderRoute('/admin/titles?tab=conflicts') // even when asked for by address
    await screen.findByText('Nothing unmatched.')
    expect(screen.queryByText('ID conflicts')).toBeNull()
    expect(screen.queryByRole('tab')).toBeNull()
    expect(calls.some((c) => c.path.includes('/conflicts'))).toBe(false) // and it never asks the server
  })

  it('puts the two lists in tabs at the top, with how many each holds, and opens on the unmatched titles', async () => {
    stubApi({ ...stubs([conflict(), conflict({ id: 8 })]), 'GET /api/admin/titles/unmatched': [
      { source: 'mal', source_id: '1', target: 'anilist', title: 'Odd One', users: 1, first_seen_at: 'x', last_seen_at: new Date().toISOString() },
    ] }, { me: admin })
    renderRoute('/admin/titles')
    const user = userEvent.setup()
    expect(await screen.findByRole('tab', { name: /unmatched titles · 1/i })).toHaveAttribute('aria-selected', 'true')
    const conflictsTab = screen.getByRole('tab', { name: /id conflicts · 2/i })
    expect(screen.getByText('Odd One')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'ID conflicts' })).toBeNull()
    await user.click(conflictsTab)
    expect(await screen.findByRole('heading', { name: 'ID conflicts' })).toBeInTheDocument()
    expect(conflictsTab).toHaveAttribute('aria-selected', 'true')
    expect(screen.queryByText('Odd One')).toBeNull()
  })
})
