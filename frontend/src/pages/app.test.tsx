import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import type { ListEntry } from '../api/types'
import { scrolled } from '../test/setup'
import { account, makeMe, renderRoute, stubApi } from '../test/harness'

beforeEach(() => localStorage.setItem('anisync-setup-done', '1'))

describe('dashboard', () => {
  it('sends users who have not finished setup to the link page, until they sync or skip', async () => {
    localStorage.clear()
    stubApi({ 'GET /api/sync/runs': [] })
    renderRoute('/dashboard')
    expect(await screen.findByRole('heading', { name: /link the lists you use/i })).toBeInTheDocument()
  })

  it('does not send someone who has already synced back into setup', async () => {
    localStorage.clear()
    const me = makeMe({ accounts: [account(1, 'anilist', { is_master: true, last_synced_at: new Date().toISOString() }), account(2, 'mal')] })
    stubApi({ 'GET /api/sync/runs': [] }, { me })
    renderRoute('/dashboard')
    expect(await screen.findByRole('heading', { name: 'Dashboard' })).toBeInTheDocument()
  })

  it('warns about a connection that expires soon and links to reconnect it', async () => {
    const me = makeMe({ accounts: [account(1, 'anilist', { is_master: true, expires_soon: true, expires_at: new Date(Date.now() + 5 * 86_400_000).toISOString() }), account(2, 'mal')] })
    stubApi({ 'GET /api/sync/runs': [] }, { me })
    renderRoute('/dashboard')
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/AniList connection expires in 5 days/)
    expect(within(alert).getByRole('link', { name: /reconnect anilist/i })).toHaveAttribute('href', '/auth/anilist/login')
  })

  it('starts a sync and shows the recent activity', async () => {
    const runs = [{ id: 1, trigger: 'manual', status: 'ok', started_at: new Date().toISOString(), finished_at: new Date().toISOString(), summary: { mal: { provider: 'mal', username: 'u', create: 2, update: 3, delete: 0, unmapped: 0, unmapped_titles: [] } } }]
    const calls = stubApi({ 'GET /api/sync/runs': runs, 'POST /api/sync': { queued: true } })
    renderRoute('/dashboard')
    expect(await screen.findByText(/MyAnimeList: 2 added, 3 updated/)).toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole('button', { name: /sync now/i }))
    await waitFor(() => expect(calls.filter((c) => c.method === 'POST' && c.path === '/api/sync')).toHaveLength(1))
  })

  it('disables Sync now while a sync is running, even one this tab did not start', async () => {
    const running = [{ id: 1, trigger: 'scheduled', status: 'running', started_at: new Date(Date.now() - 60_000).toISOString(), finished_at: null, summary: {}, planned: 0, processed: 0 }]
    stubApi({ 'GET /api/sync/runs': running })
    renderRoute('/dashboard')
    const button = await screen.findByRole('button', { name: /syncing/i })
    expect(button).toBeDisabled()
    expect(screen.getByText('Running')).toBeInTheDocument()
  })

  it('shows the server message when a sync is already running', async () => {
    stubApi({ 'GET /api/sync/runs': [], 'POST /api/sync': { __status: 409, detail: 'A sync is already running.' } })
    renderRoute('/dashboard')
    await userEvent.setup().click(await screen.findByRole('button', { name: /sync now/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent('A sync is already running.')
  })

  it('explains titles a provider hides instead of calling them failures', async () => {
    const runs = [{ id: 1, trigger: 'manual', status: 'ok', started_at: new Date().toISOString(), finished_at: new Date().toISOString(), planned: 3, processed: 3, summary: { mal: { provider: 'mal', username: 'u', create: 3, update: 0, delete: 0, unmapped: 0, unmapped_titles: [], errors: [], skipped: 2, skipped_reason: 'Kitsu hides adult titles while your SFW filter is on' } } }]
    stubApi({ 'GET /api/sync/runs': runs })
    renderRoute('/dashboard')
    expect(await screen.findByText(/2 titles were skipped: Kitsu hides adult titles/)).toBeInTheDocument()
    expect(screen.getByText('In sync')).toBeInTheDocument()
    expect(screen.queryByText(/failed/)).toBeNull()
  })

  it('tells the user when a mirror hides adult titles and what to change in that provider', async () => {
    const me = makeMe({ accounts: [account(1, 'anilist', { is_master: true }), account(3, 'kitsu', { hides_adult: true })] })
    stubApi({ 'GET /api/sync/runs': [] }, { me })
    renderRoute('/dashboard')
    const note = await screen.findByRole('note')
    expect(note).toHaveTextContent('Kitsu is hiding adult titles from AniSync.')
    expect(note).toHaveTextContent('In Kitsu, open Settings → Adult Content and allow adult titles')
    expect(note).toHaveTextContent('Until then they are skipped.')
  })

  it('says a master that hides adult titles will never cause removals', async () => {
    const me = makeMe({ master_account_id: 3, accounts: [account(3, 'kitsu', { is_master: true, hides_adult: true }), account(1, 'anilist')] })
    stubApi({ 'GET /api/sync/runs': [] }, { me })
    renderRoute('/dashboard')
    expect(await screen.findByRole('note')).toHaveTextContent(/never remove anything from your other lists/i)
  })

  it('lets the user say they changed the provider setting, checks once and reports the answer', async () => {
    const hidden = makeMe({ accounts: [account(1, 'anilist', { is_master: true }), account(3, 'kitsu', { hides_adult: true })] })
    const calls = stubApi({ 'GET /api/sync/runs': [], 'POST /api/accounts/3/recheck-adult': hidden }, { me: hidden })
    renderRoute('/dashboard')
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /i've changed it, check again/i }))
    expect(await screen.findByText(/still hidden/i)).toBeInTheDocument()
    expect(calls.filter((c) => c.path === '/api/accounts/3/recheck-adult')).toHaveLength(1) // one request, not a poll
  })

  it('removes the notice once the provider no longer hides adult titles', async () => {
    const hidden = makeMe({ accounts: [account(1, 'anilist', { is_master: true }), account(3, 'kitsu', { hides_adult: true })] })
    const fixed = makeMe({ accounts: [account(1, 'anilist', { is_master: true }), account(3, 'kitsu', { hides_adult: false })] })
    stubApi({ 'GET /api/sync/runs': [], 'POST /api/accounts/3/recheck-adult': fixed }, { me: hidden })
    renderRoute('/dashboard')
    await userEvent.setup().click(await screen.findByRole('button', { name: /i've changed it, check again/i }))
    await waitFor(() => expect(screen.queryByRole('note')).toBeNull())
  })

  it('says how many adult titles were left out of a mirror as the user chose', async () => {
    const runs = [{ id: 1, trigger: 'manual', status: 'ok', started_at: new Date().toISOString(), finished_at: new Date().toISOString(), planned: 0, processed: 0, summary: { mal: { provider: 'mal', username: 'u', create: 0, update: 0, delete: 0, unmapped: 0, unmapped_titles: [], errors: [], adult_skipped: 12 } } }]
    stubApi({ 'GET /api/sync/runs': runs })
    renderRoute('/dashboard')
    expect(await screen.findByText(/12 adult titles are not copied, as you chose/)).toBeInTheDocument()
  })

  it('opens the preview inside the app, not the setup flow', async () => {
    stubApi({ 'GET /api/sync/runs': [] })
    renderRoute('/dashboard')
    expect(await screen.findByRole('link', { name: /preview changes/i })).toHaveAttribute('href', '/preview')
  })

  it('shows no adult-content notice when nothing is hidden', async () => {
    stubApi({ 'GET /api/sync/runs': [] })
    renderRoute('/dashboard')
    await screen.findByRole('heading', { name: 'Dashboard' })
    expect(screen.queryByRole('note')).toBeNull()
  })

  it('shows how far a running sync has got', async () => {
    const running = [{ id: 1, trigger: 'manual', status: 'running', started_at: new Date(Date.now() - 60_000).toISOString(), finished_at: null, summary: {}, planned: 300, processed: 40 }]
    stubApi({ 'GET /api/sync/runs': running })
    renderRoute('/dashboard')
    expect(await screen.findByRole('button', { name: /syncing 40 of 300/i })).toBeDisabled()
    expect(screen.getByText('Running · 40 of 300')).toBeInTheDocument()
  })

  it('shows a run that stopped halfway as interrupted, not running', async () => {
    const stopped = [{ id: 1, trigger: 'manual', status: 'interrupted', started_at: new Date(Date.now() - 3_600_000).toISOString(), finished_at: new Date().toISOString(), summary: {}, planned: 300, processed: 40 }]
    stubApi({ 'GET /api/sync/runs': stopped })
    renderRoute('/dashboard')
    expect(await screen.findByText('Interrupted')).toBeInTheDocument()
    expect(screen.queryByText(/running/i)).toBeNull()
    expect(screen.getByRole('button', { name: /sync now/i })).toBeEnabled()
  })

  it('does not let a run that never finished block the button forever', async () => {
    const stale = [{ id: 1, trigger: 'scheduled', status: 'running', started_at: new Date(Date.now() - 20 * 60_000).toISOString(), finished_at: null, summary: {}, planned: 0, processed: 0 }]
    stubApi({ 'GET /api/sync/runs': stale })
    renderRoute('/dashboard')
    expect(await screen.findByRole('button', { name: /sync now/i })).toBeEnabled()
  })

  it('marks a mirror whose last run failed', async () => {
    const runs = [{ id: 1, trigger: 'scheduled', status: 'ok', started_at: new Date().toISOString(), finished_at: new Date().toISOString(), summary: { mal: { provider: 'mal', username: 'u', create: 0, update: 0, delete: 0, unmapped: 4, unmapped_titles: [], errors: ['x', 'y'] } } }]
    stubApi({ 'GET /api/sync/runs': runs })
    renderRoute('/dashboard')
    expect(await screen.findByText(/2 failed/)).toBeInTheDocument()
  })
})

const entry = (id: string, title: string, over: Partial<ListEntry> = {}): ListEntry => ({
  media_id: id, status: 'watching', progress: 3, score: 80, rewatching: false, started_at: null, finished_at: null, updated_at: null,
  title, total_episodes: 12, image_url: null, adult: false, ...over,
})
const ENTRIES = [entry('1', 'Frieren'), entry('2', 'Dandadan', { status: 'completed', score: null }), entry('3', 'Bocchi the Rock!', { status: 'on_hold' })]

describe('my list', () => {
  it('shows the entries with counts, and filters by status and search', async () => {
    stubApi({ 'GET /api/list': ENTRIES })
    renderRoute('/list')
    const user = userEvent.setup()
    expect(await screen.findByText('Frieren')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /all 3/i })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /completed 1/i }))
    expect(screen.queryByText('Frieren')).toBeNull()
    expect(screen.getByText('Dandadan')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /all 3/i }))
    await user.type(screen.getByRole('searchbox', { name: /search your list/i }), 'bocchi')
    expect(screen.getByText('Bocchi the Rock!')).toBeInTheDocument()
    expect(screen.queryByText('Frieren')).toBeNull()
  })

  it('saves an edit with the score on the 0-100 scale, and can undo it', async () => {
    const calls = stubApi({ 'GET /api/list': ENTRIES, 'PUT /api/list/1': { ok: true, queued_mirrors: 1 } })
    renderRoute('/list')
    const user = userEvent.setup()
    await user.click(await screen.findByText('Frieren'))
    const score = screen.getByLabelText('Score')
    await user.clear(score)
    await user.type(score, '9.5')
    await user.selectOptions(screen.getByLabelText('Status'), 'completed')
    await user.click(screen.getByRole('button', { name: /one episode more/i }))
    await user.click(screen.getByRole('button', { name: /^save$/i }))
    const put = await waitFor(() => {
      const c = calls.find((x) => x.method === 'PUT' && x.path === '/api/list/1')
      expect(c).toBeDefined()
      return c!
    })
    expect(put.body).toMatchObject({ status: 'completed', progress: 4, score: 95, rewatching: false })
    expect(await screen.findByText(/saved to anilist\. updating 1 mirror/i)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /undo/i }))
    await waitFor(() => expect(calls.filter((c) => c.method === 'PUT')).toHaveLength(2))
    expect(calls.filter((c) => c.method === 'PUT')[1].body).toMatchObject({ status: 'watching', progress: 3, score: 80 })
  })

  it('blurs the covers of adult titles and marks them 18+, until the user chooses to see them', async () => {
    const adultList = [entry('1', 'Safe Show', { image_url: 'https://s4.anilist.co/a.jpg' }), entry('2', 'Adult Show', { adult: true, image_url: 'https://s4.anilist.co/b.jpg' })]
    stubApi({ 'GET /api/list': adultList })
    renderRoute('/list')
    await screen.findByText('Adult Show')
    const hidden = screen.getByRole('img', { name: /cover hidden: adult title/i })
    expect(hidden).toHaveTextContent('18+')
    expect(hidden.querySelector('img')).toHaveStyle({ filter: 'blur(10px) brightness(.8)' })
    const safe = screen.getByRole('img', { name: /cover: safe show/i })
    expect(safe.querySelector('img')).not.toHaveStyle({ filter: 'blur(10px) brightness(.8)' })
    expect(safe).not.toHaveTextContent('18+')
  })

  it('shows adult covers when the user turned that on', async () => {
    const adultList = [entry('2', 'Adult Show', { adult: true, image_url: 'https://s4.anilist.co/b.jpg' })]
    stubApi({ 'GET /api/list': adultList }, { me: makeMe({ show_adult_covers: true }) })
    renderRoute('/list')
    await screen.findByText('Adult Show')
    expect(screen.getByRole('img', { name: /cover: adult show/i })).not.toHaveTextContent('18+')
    expect(screen.queryByRole('img', { name: /cover hidden/i })).toBeNull()
  })

  it('shows the total next to the episode count, as "3 / 12", in the list and in the editor', async () => {
    stubApi({ 'GET /api/list': ENTRIES })
    renderRoute('/list')
    const user = userEvent.setup()
    expect((await screen.findAllByText(/3 \/ 12 episodes/)).length).toBeGreaterThan(0)
    await user.click(screen.getByText('Frieren'))
    const field = screen.getByLabelText('Episodes watched')
    expect(field).toHaveValue(3)
    expect(screen.getByText('/ 12')).toBeInTheDocument()
    expect(field).toHaveAccessibleDescription('/ 12')
  })

  it('has no total to show for a title without a known episode count', async () => {
    stubApi({ 'GET /api/list': [entry('7', 'Open Ended', { total_episodes: null })] })
    renderRoute('/list')
    await userEvent.setup().click(await screen.findByText('Open Ended'))
    expect(screen.getByLabelText('Episodes watched')).toBeInTheDocument()
    expect(screen.queryByText(/^\/ \d+/)).toBeNull()
  })

  it('keeps the editor in view while the list scrolls, and brings it into view when a title is chosen', async () => {
    stubApi({ 'GET /api/list': ENTRIES })
    renderRoute('/list')
    await userEvent.setup().click(await screen.findByText('Frieren'))
    const panel = screen.getByRole('complementary')
    expect(panel).toHaveStyle({ position: 'sticky', top: '16px' })
    expect(scrolled).toContain(panel)
  })

  it('rejects a score above 10 before sending anything', async () => {
    const calls = stubApi({ 'GET /api/list': ENTRIES })
    renderRoute('/list')
    const user = userEvent.setup()
    await user.click(await screen.findByText('Frieren'))
    const score = screen.getByLabelText('Score')
    await user.clear(score)
    await user.type(score, '11')
    expect(screen.getByRole('button', { name: /^save$/i })).toBeDisabled()
    expect(screen.getByText(/use a number from 0 to 10/i)).toBeInTheDocument()
    expect(calls.some((c) => c.method === 'PUT')).toBe(false)
  })

  it('asks before removing an entry', async () => {
    const calls = stubApi({ 'GET /api/list': ENTRIES, 'DELETE /api/list/1': { ok: true } })
    renderRoute('/list')
    const user = userEvent.setup()
    await user.click(await screen.findByText('Frieren'))
    await user.click(screen.getByRole('button', { name: /^remove$/i }))
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false)
    await user.click(screen.getByRole('button', { name: /yes, remove/i }))
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE' && c.path === '/api/list/1')).toBe(true))
  })

  it('shows the server message when the list cannot be loaded', async () => {
    stubApi({ 'GET /api/list': { __status: 409, detail: 'mal rejected the access token' } })
    renderRoute('/list')
    expect(await screen.findByText('mal rejected the access token')).toBeInTheDocument()
  })
})

describe('settings', () => {
  it('shows the adult-content notice next to the account that hides titles', async () => {
    const me = makeMe({ accounts: [account(1, 'anilist', { is_master: true }), account(3, 'kitsu', { hides_adult: true })] })
    stubApi({ 'GET /api/sessions': [] }, { me })
    renderRoute('/settings')
    expect(await screen.findByRole('note')).toHaveTextContent('Kitsu is hiding adult titles from AniSync.')
  })

  const sessions = [{ id: 1, created_at: 'x', last_seen_at: new Date().toISOString(), user_agent: 'Firefox', current: true }, { id: 2, created_at: 'x', last_seen_at: new Date().toISOString(), user_agent: 'Phone', current: false }]

  it('saves the display name', async () => {
    const calls = stubApi({ 'GET /api/sessions': sessions, 'PATCH /api/me': (b) => makeMe({ display_name: b.display_name as string }) })
    renderRoute('/settings')
    const user = userEvent.setup()
    const name = await screen.findByLabelText(/display name/i)
    expect(screen.getByRole('button', { name: /save name/i })).toBeDisabled() // unchanged
    await user.clear(name)
    await user.type(name, 'Kaito the Great')
    await user.click(screen.getByRole('button', { name: /save name/i }))
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ display_name: 'Kaito the Great' }))
  })

  it('saves the choice to show adult covers', async () => {
    const calls = stubApi({ 'GET /api/sessions': sessions, 'PATCH /api/me': makeMe({ show_adult_covers: true }) })
    renderRoute('/settings')
    await userEvent.setup().click(await screen.findByRole('switch', { name: /show covers of adult titles/i }))
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH' && c.path === '/api/me')?.body).toEqual({ show_adult_covers: true }))
  })

  it('saves the master-list choice to ignore adult titles', async () => {
    const calls = stubApi({ 'GET /api/sessions': sessions, 'PATCH /api/me': makeMe({ ignore_adult_titles: true }) })
    renderRoute('/settings')
    const user = userEvent.setup()
    const toggle = await screen.findByRole('switch', { name: /ignore adult titles on my master list/i })
    expect(toggle).not.toBeChecked()
    await user.click(toggle)
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH' && c.path === '/api/me')?.body).toEqual({ ignore_adult_titles: true }))
    expect(screen.getByText(/never removed/i)).toBeInTheDocument()
  })

  it('saves the per-mirror choice to leave adult titles out', async () => {
    const calls = stubApi({ 'GET /api/sessions': sessions, 'PATCH /api/accounts/2': makeMe() })
    renderRoute('/settings')
    await userEvent.setup().click(await screen.findByRole('switch', { name: /don't copy adult titles to myanimelist/i }))
    await waitFor(() => expect(calls.find((c) => c.path === '/api/accounts/2')?.body).toMatchObject({ skip_adult: true }))
  })

  it('saves the theme to the account and applies it', async () => {
    const calls = stubApi({ 'GET /api/sessions': sessions, 'PATCH /api/me': makeMe({ theme: 'dark' }) })
    renderRoute('/settings')
    await userEvent.setup().click(await screen.findByRole('button', { name: 'Dark' }))
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ theme: 'dark' }))
    expect(document.documentElement.className).toBe('as theme-dark')
  })

  it('only enables account deletion after typing the confirmation phrase', async () => {
    const calls = stubApi({ 'GET /api/sessions': sessions, 'POST /api/me/delete': { ok: true } })
    renderRoute('/settings')
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /^delete account$/i }))
    const confirm = screen.getByRole('button', { name: /delete everything/i })
    expect(confirm).toBeDisabled()
    await user.type(screen.getByLabelText(/type "delete my account"/i), 'delete my')
    expect(confirm).toBeDisabled()
    await user.type(screen.getByLabelText(/type "delete my account"/i), ' account')
    expect(confirm).toBeEnabled()
    await user.click(confirm)
    await waitFor(() => expect(calls.find((c) => c.path === '/api/me/delete')?.body).toEqual({ confirm: 'delete my account' }))
  })

  it('asks before unlinking an account and makes another list the master', async () => {
    const calls = stubApi({ 'GET /api/sessions': sessions, 'DELETE /api/accounts/2': makeMe(), 'PUT /api/me/master': makeMe() })
    renderRoute('/settings')
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /make master/i }))
    await waitFor(() => expect(calls.find((c) => c.path === '/api/me/master')?.body).toEqual({ account_id: 2 }))
    const [, unlinkMirror] = screen.getAllByRole('button', { name: /^unlink$/i }) // AniList (master) first, then MyAnimeList
    await user.click(unlinkMirror)
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false)
    await user.click(screen.getByRole('button', { name: /yes, unlink/i }))
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE' && c.path === '/api/accounts/2')).toBe(true))
  })
})
