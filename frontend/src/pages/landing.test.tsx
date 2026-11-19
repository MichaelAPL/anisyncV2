import { screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { PROVIDERS, renderRoute, stubApi } from '../test/harness'

describe('landing page', () => {
  it('offers sign-in for the providers that allow it and explains how to add the others', async () => {
    stubApi({}, { me: null })
    renderRoute('/')
    const mal = await screen.findByRole('link', { name: /continue with myanimelist/i })
    expect(mal).toHaveAttribute('href', '/auth/mal/login')
    expect(screen.getByRole('link', { name: /continue with anilist/i })).toHaveAttribute('href', '/auth/anilist/login')
    expect(screen.queryByRole('link', { name: /continue with kitsu/i })).toBeNull()
    expect(screen.getByText(/using kitsu\?/i)).toBeInTheDocument()
  })

  it('disables a paused provider and says why', async () => {
    const paused = PROVIDERS.map((p) => (p.id === 'mal' ? { ...p, paused: true, paused_reason: 'upstream outage' } : p))
    stubApi({ 'GET /api/providers': paused }, { me: null })
    renderRoute('/')
    const mal = (await screen.findByText(/continue with myanimelist/i)).closest('a')!
    expect(mal).not.toHaveAttribute('href')
    expect(mal).toHaveAttribute('aria-disabled', 'true')
    expect(screen.getByRole('status')).toHaveTextContent(/MyAnimeList is paused for now: upstream outage/)
  })

  it('sends a signed-in user to the dashboard', async () => {
    localStorage.setItem('anisync-setup-done', '1')
    stubApi({ 'GET /api/sync/runs': [] })
    renderRoute('/')
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Dashboard' })).toBeInTheDocument())
  })

  it('shows the maintenance page instead of the site, but not for admins', async () => {
    const maintenance = { maintenance: true, maintenance_message: 'Back at 14:00', banner_text: null, banner_level: 'info', updated_at: 'x' }
    stubApi({ 'GET /api/site': maintenance }, { me: null })
    renderRoute('/')
    expect(await screen.findByRole('heading', { name: /down for maintenance/i })).toBeInTheDocument()
    expect(screen.getByText(/back at 14:00/i)).toBeInTheDocument()
  })
})
