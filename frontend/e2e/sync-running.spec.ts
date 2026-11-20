import { API, entries, expect, finishSetup, reset, seed, signIn, test } from './support'

test.beforeEach(async ({ request }) => reset(request))

test('while a sync runs the button and the preview hold, and everything frees up when it is done', async ({ page, context, request }) => {
  const token = await seed(request, { name: 'busy', accounts: ['mal', 'anilist'], lists: { mal: entries(24, (n) => ({ progress: n % 12 })) } })
  await signIn(context, token)
  await finishSetup(page)
  await request.post(`${API}/__e2e/config`, { data: { save_delay: 0.4 } }) // 24 writes: about ten seconds

  await page.goto('/dashboard')
  await page.getByRole('button', { name: /sync now/i }).click()
  const syncing = page.getByRole('button', { name: /syncing/i })
  await expect(syncing).toBeVisible()
  await expect(syncing).toBeDisabled()

  await page.getByRole('link', { name: /preview changes/i }).click()
  await expect(page.getByText(/a sync is running right now/i)).toBeVisible()
  // Wait until the preview itself has loaded: a button that is disabled because it is still loading proves nothing.
  await expect(page.getByRole('heading', { name: /changes to anilist/i })).toBeVisible()
  await expect(page.getByRole('button', { name: /a sync is running/i })).toBeDisabled()

  await page.getByRole('link', { name: /back to dashboard/i }).click()
  await expect(page.getByRole('button', { name: /sync now/i })).toBeEnabled({ timeout: 40_000 })
  await expect(page.getByText('Done').first()).toBeVisible()
})
