import type { APIRequestContext, BrowserContext, Page } from '@playwright/test'
import { entries, expect, finishSetup, reset, seed, signIn, test } from './support'

test.beforeEach(async ({ request }) => reset(request))

async function openList(page: Page, context: BrowserContext, request: APIRequestContext, name: string) {
  const token = await seed(request, {
    name,
    accounts: ['mal', 'anilist'],
    lists: { mal: entries(60, (n) => ({ total: 12, adult: n % 7 === 0, score: 80 })) },
  })
  await signIn(context, token)
  await finishSetup(page)
  await page.goto('/list')
  await expect(page.getByText('Title 1', { exact: true })).toBeVisible()
}

test('the editor opens where the user is looking, whatever the screen', async ({ page, context, request }, info) => {
  await openList(page, context, request, 'edit')
  const phone = info.project.name.startsWith('phone')
  await page.getByText('Title 40', { exact: true }).scrollIntoViewIfNeeded()
  await page.getByText('Title 40', { exact: true }).click()
  if (phone) {
    const sheet = page.getByRole('dialog')
    await expect(sheet).toBeVisible()
    await expect(sheet.getByText('/ 12')).toBeVisible()
    await expect(sheet).toBeInViewport()
    await sheet.getByRole('button', { name: /close editor/i }).click()
    await expect(sheet).toHaveCount(0)
  } else {
    const panel = page.getByRole('complementary')
    await expect(panel).toBeInViewport({ ratio: 0.5 })
    await page.mouse.wheel(0, 700) // keeps following the scroll
    await expect(panel).toBeInViewport({ ratio: 0.5 })
    await expect(panel.getByLabel('Episodes watched')).toHaveValue(/\d+/)
    await expect(panel.getByText('/ 12')).toBeVisible()
  }
})

test('saving an edit sends it to the master and shows undo', async ({ page, context, request }) => {
  await openList(page, context, request, 'save')
  await page.getByText('Title 3', { exact: true }).click()
  const form = page.getByRole('dialog').or(page.getByRole('complementary'))
  await form.getByLabel('Status').selectOption('completed')
  await form.getByRole('button', { name: /^save$/i }).click()
  await expect(page.getByText(/saved to myanimelist/i)).toBeVisible()
  await expect(page.getByRole('button', { name: /undo/i })).toBeVisible()
})

test('covers of adult titles are blurred and marked 18+ until the user chooses to see them', async ({ page, context, request }) => {
  await openList(page, context, request, 'blur')
  const hidden = page.getByRole('img', { name: /cover hidden: adult title/i }).first()
  await expect(hidden).toBeVisible()
  await expect(hidden).toContainText('18+')
  const filter = await hidden.locator('img').evaluate((img) => getComputedStyle(img).filter)
  expect(filter).toContain('blur')

  await page.goto('/settings')
  await page.getByRole('switch', { name: /show covers of adult titles/i }).click()
  await expect(page.getByRole('switch', { name: /show covers of adult titles/i })).toBeChecked()
  await page.goto('/list')
  await expect(page.getByRole('img', { name: /cover hidden/i })).toHaveCount(0)
})
