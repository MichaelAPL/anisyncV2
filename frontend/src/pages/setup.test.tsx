import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import type { MirrorResult } from '../api/types'
import { account, makeMe, renderRoute, stubApi } from '../test/harness'

const oneAccount = makeMe({ accounts: [account(1, 'anilist', { is_master: true })] })

describe('link lists', () => {
  it('needs two linked lists before continuing, and can be skipped', async () => {
    stubApi({}, { me: oneAccount })
    renderRoute('/link')
    expect(await screen.findByText(/link one more list to continue/i)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /continue/i })).toBeNull()
    expect(screen.getByRole('link', { name: /connect myanimelist/i })).toHaveAttribute('href', '/auth/mal/login')
    expect(screen.getByRole('button', { name: /skip for now/i })).toBeInTheDocument()
  })

  it('continues to the master choice with two lists', async () => {
    stubApi({})
    renderRoute('/link')
    expect(await screen.findByRole('link', { name: /continue/i })).toHaveAttribute('href', '/master')
    expect(screen.getAllByText(/linked/i).length).toBeGreaterThan(0)
  })

  it('links Kitsu with a username and password, and shows the server message when it fails', async () => {
    const calls = stubApi({ 'POST /auth/kitsu/password': { __status: 400, detail: 'Kitsu rejected those credentials' } })
    renderRoute('/link')
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText(/kitsu email/i), 'me@example.com')
    await user.type(screen.getByLabelText(/^password$/i), 'hunter2')
    await user.click(screen.getByRole('button', { name: /link kitsu/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Kitsu rejected those credentials')
    expect(calls.find((c) => c.path === '/auth/kitsu/password')?.body).toEqual({ username: 'me@example.com', password: 'hunter2' })
    expect(screen.getByLabelText(/^password$/i)).toHaveValue('hunter2') // a failed attempt keeps the form as it was
  })

  it('warns on the link page when a linked Kitsu account hides adult titles', async () => {
    const me = makeMe({ accounts: [account(1, 'anilist', { is_master: true }), account(3, 'kitsu', { hides_adult: true })] })
    stubApi({}, { me })
    renderRoute('/link')
    expect(await screen.findByRole('note')).toHaveTextContent(/Settings → Adult Content/)
  })

  it('shows what went wrong when an OAuth round trip comes back with an error', async () => {
    stubApi({}, { me: oneAccount })
    renderRoute('/link?error=account_already_linked')
    expect(await screen.findByRole('alert')).toHaveTextContent(/already linked to another anisync user/i)
  })
})

describe('choose master', () => {
  it('changes the master and the mirror switches on the server', async () => {
    const calls = stubApi({
      'PUT /api/me/master': (body) => makeMe({ master_account_id: body.account_id as number, accounts: [account(1, 'anilist'), account(2, 'mal', { is_master: true })] }),
      'PATCH /api/accounts/2': makeMe(),
    })
    renderRoute('/master')
    const user = userEvent.setup()
    const radios = await screen.findAllByRole('radio')
    expect(radios[0]).toBeChecked()
    await user.click(radios[1])
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT' && c.path === '/api/me/master')).toBe(true))
    expect(calls.find((c) => c.path === '/api/me/master')?.body).toEqual({ account_id: 2 })
    await waitFor(() => expect(radios[1]).toBeChecked())
  })

  it('offers to leave adult titles out of a mirror', async () => {
    const calls = stubApi({ 'PATCH /api/accounts/2': makeMe() })
    renderRoute('/master')
    await userEvent.setup().click(await screen.findByRole('switch', { name: /don't copy adult titles/i }))
    await waitFor(() => expect(calls.find((c) => c.path === '/api/accounts/2')?.body).toMatchObject({ skip_adult: true }))
  })
})

const preview: Record<string, MirrorResult> = {
  mal: {
    provider: 'mal', username: 'u', create: 2, update: 1, delete: 0, unmapped: 1, unmapped_titles: ['Some Music Video'],
    changes: [
      { op: 'create', media_id: '1', fields: [], title: 'Dandadan' },
      { op: 'create', media_id: '2', fields: [], title: 'Frieren' },
      { op: 'update', media_id: '3', fields: ['status', 'score'], title: 'Cowboy Bebop' },
    ],
  },
}

describe('review first sync', () => {
  it('shows a loading view while the preview is read, then the changes', async () => {
    let release: (v: unknown) => void = () => {}
    stubApi({ 'GET /api/sync/preview': () => new Promise((r) => (release = r)) })
    renderRoute('/review')
    expect(await screen.findByText(/reading your master list/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /apply/i })).toBeDisabled()
    release(preview)
    expect(await screen.findByText('Dandadan')).toBeInTheDocument()
    expect(screen.queryByText(/reading your master list/i)).toBeNull()
    expect(screen.getByRole('button', { name: /apply to myanimelist/i })).toBeEnabled()
  })

  it('filters the changes, lists the titles that could not be matched, and applies once', async () => {
    const calls = stubApi({ 'GET /api/sync/preview': preview, 'POST /api/sync': { queued: true }, 'GET /api/sync/runs': [] })
    renderRoute('/review')
    const user = userEvent.setup()
    await screen.findByText('Dandadan')
    await user.click(screen.getByRole('button', { name: /updates/i }))
    expect(screen.getByText('Cowboy Bebop')).toBeInTheDocument()
    expect(screen.queryByText('Dandadan')).toBeNull()
    expect(screen.getByText(/changes: status, score/i)).toBeInTheDocument()
    expect(screen.getByText(/1 title has no myanimelist match/i)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /apply to myanimelist/i }))
    await waitFor(() => expect(localStorage.getItem('anisync-setup-done')).toBe('1'))
    expect(calls.filter((c) => c.method === 'POST' && c.path === '/api/sync')).toHaveLength(1)
  })

  it('shows the server message when the preview fails and offers a retry', async () => {
    stubApi({ 'GET /api/sync/preview': { __status: 409, detail: 'mal rejected the access token' } })
    renderRoute('/review')
    expect(await screen.findByText('mal rejected the access token')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /apply/i })).toBeDisabled()
  })

  it('says how many adult titles are not copied because the user chose that', async () => {
    stubApi({ 'GET /api/sync/preview': { mal: { ...preview.mal, adult_skipped: 3 } } })
    renderRoute('/review')
    expect(await screen.findByText(/3 adult titles are not copied to myanimelist, as you chose/i)).toBeInTheDocument()
  })

  it('holds Apply and says why while a sync is already running', async () => {
    const running = [{ id: 1, trigger: 'manual', status: 'running', started_at: new Date(Date.now() - 30_000).toISOString(), finished_at: null, summary: {}, planned: 10, processed: 3 }]
    stubApi({ 'GET /api/sync/preview': preview, 'GET /api/sync/runs': running })
    renderRoute('/review')
    await screen.findByText('Dandadan')
    expect(screen.getByText(/a sync is running right now/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /a sync is running/i })).toBeDisabled()
  })

  it('does not offer to apply while a mirror reports an error', async () => {
    stubApi({ 'GET /api/sync/preview': { mal: { ...preview.mal, error: 'boom', reauth_required: true } } })
    renderRoute('/review')
    expect(await screen.findByText(/connection expired/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /apply/i })).toBeDisabled()
  })
})

describe('preview from the dashboard', () => {
  it('is part of the app, not the setup stepper, and goes back to the dashboard', async () => {
    localStorage.setItem('anisync-setup-done', '1')
    stubApi({ 'GET /api/sync/preview': preview, 'GET /api/sync/runs': [] })
    renderRoute('/preview')
    expect(await screen.findByRole('heading', { name: 'Preview changes' })).toBeInTheDocument()
    await screen.findByText('Dandadan')
    expect(screen.queryByText(/step 3 of 3/i)).toBeNull()
    expect(screen.queryByRole('progressbar')).toBeNull()
    expect(screen.getByRole('link', { name: /back to dashboard/i })).toHaveAttribute('href', '/dashboard')
    expect(screen.getByRole('button', { name: /apply to myanimelist/i })).toBeEnabled()
  })

  it('cannot start another sync while one is running', async () => {
    localStorage.setItem('anisync-setup-done', '1')
    const running = [{ id: 1, trigger: 'scheduled', status: 'running', started_at: new Date(Date.now() - 30_000).toISOString(), finished_at: null, summary: {}, planned: 0, processed: 0 }]
    stubApi({ 'GET /api/sync/preview': preview, 'GET /api/sync/runs': running })
    renderRoute('/preview')
    await screen.findByText('Dandadan')
    expect(screen.getByRole('button', { name: /a sync is running/i })).toBeDisabled()
  })
})
