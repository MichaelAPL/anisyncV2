import { entries, expect, remote, reset, seed, signIn, test } from './support'

test.beforeEach(async ({ request }) => reset(request))

test('a new user links, picks a master, previews and applies; the mirror ends up matching', async ({ page, context, request }) => {
  const token = await seed(request, {
    name: 'flow',
    master: 'mal',
    accounts: ['mal', 'anilist'],
    lists: { mal: entries(5, (n) => ({ status: n % 2 ? 'watching' : 'completed', progress: n })) },
  })
  await signIn(context, token)

  await page.goto('/dashboard') // not finished with setup: goes to the link page
  await expect(page).toHaveURL(/\/link$/)
  await expect(page.getByRole('heading', { name: /link the lists you use/i })).toBeVisible()
  await page.getByRole('link', { name: /continue/i }).click()

  await expect(page.getByRole('heading', { name: /source of truth/i })).toBeVisible()
  await expect(page.getByRole('radio').first()).toBeChecked() // the master the account started with (MAL)
  await page.getByRole('button', { name: /preview first sync/i }).click()

  await expect(page.getByRole('heading', { name: /here's what will change/i })).toBeVisible()
  await expect(page.getByText('Title 1', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: /apply to anilist/i }).click()

  await expect(page).toHaveURL(/\/dashboard$/)
  await expect(page.getByText('Done').first()).toBeVisible({ timeout: 30_000 })
  const mirror = await remote(request, 'anilist', 'flow')
  expect(Object.keys(mirror)).toHaveLength(5)
  expect(mirror.a3).toMatchObject({ status: 'watching', progress: 3 })
})

test('the setup stepper is not shown when previewing from the dashboard', async ({ page, context, request }) => {
  const token = await seed(request, { name: 'prev', accounts: ['mal', 'anilist'], lists: { mal: entries(3) } })
  await signIn(context, token)
  await page.addInitScript(() => localStorage.setItem('anisync-setup-done', '1'))
  await page.goto('/dashboard')
  await page.getByRole('link', { name: /preview changes/i }).click()
  await expect(page).toHaveURL(/\/preview$/)
  await expect(page.getByRole('heading', { name: 'Preview changes' })).toBeVisible()
  await expect(page.getByText(/step 3 of 3/i)).toHaveCount(0)
  await expect(page.getByRole('link', { name: /back to dashboard/i })).toBeVisible()
})
