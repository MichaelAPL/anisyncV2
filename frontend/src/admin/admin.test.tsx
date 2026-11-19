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
