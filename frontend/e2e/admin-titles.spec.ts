import { expect, finishSetup, reset, seed, seedConflicts, signIn, test } from './support'

test.beforeEach(async ({ request }) => reset(request))

test('an admin can find the ID conflicts, decide on one, and download the report', async ({ page, context, request }) => {
  const token = await seed(request, { name: 'titles', role: 'admin', accounts: ['mal', 'kitsu'] })
  await signIn(context, token)
  await finishSetup(page)
  await seedConflicts(request)

  await page.goto('/admin/titles')
  const conflictsTab = page.getByRole('tab', { name: /id conflicts · 2/i })
  await expect(conflictsTab).toBeVisible() // visible without scrolling, with how many there are
  await expect(conflictsTab).toBeInViewport()
  await conflictsTab.click()

  await expect(page.getByRole('heading', { name: 'ID conflicts' })).toBeVisible()
  await expect(page.getByText('Conflict Alpha').first()).toBeVisible()
  await expect(page.getByRole('link', { name: /download report/i })).toHaveAttribute('href', /report\.csv\?status=open/)

  await page.getByLabel('Note').first().fill('checked: the dataset is right')
  await page.getByRole('button', { name: /keep the dataset's answer/i }).first().click()
  await expect(page.getByRole('button', { name: /^open 1$/i })).toBeVisible()
  await expect(page.getByRole('button', { name: /^kept 1$/i })).toBeVisible()

  // The report downloads through the browser session and lists the conflicts, the decided one with its note.
  const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('link', { name: /download report/i }).click()])
  expect(download.suggestedFilename()).toBe('anisync-id-conflicts.csv')
  const path = await download.path()
  const { readFileSync } = await import('node:fs')
  const csv = readFileSync(path, 'utf8')
  expect(csv).toContain('kind,status,seen')
  expect(csv).toContain('Conflict Alpha')
})

test('the tab is part of the address, so a link opens the conflicts directly', async ({ page, context, request }) => {
  const token = await seed(request, { name: 'titles2', role: 'admin', accounts: ['mal', 'kitsu'] })
  await signIn(context, token)
  await seedConflicts(request)
  await page.goto('/admin/titles?tab=conflicts')
  await expect(page.getByRole('heading', { name: 'ID conflicts' })).toBeVisible()
  await expect(page.getByRole('tab', { name: /id conflicts/i })).toHaveAttribute('aria-selected', 'true')
})
