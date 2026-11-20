import { expect, overflowsHorizontally, reset, test } from './support'

test.beforeEach(async ({ request }) => reset(request))

test('the landing page shows the sign-in buttons, a poster wall, and fits the screen', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { level: 1 })).toContainText('One anime list')
  await expect(page.getByRole('link', { name: /continue with myanimelist/i })).toHaveAttribute('href', '/auth/mal/login')
  await expect(page.getByRole('link', { name: /continue with anilist/i })).toHaveAttribute('href', '/auth/anilist/login')
  await expect(page.getByText(/using kitsu\?/i)).toBeVisible()
  await expect(page.locator('.wall-track img').first()).toBeAttached()
  expect(await overflowsHorizontally(page)).toMatchObject({ over: false })
})

test('the poster wall drifts, and stops for people who asked for less motion', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('.wall-track').first()).toBeAttached()
  const running = () => page.evaluate(() => document.getAnimations().filter((a) => a.playState === 'running').length)
  await expect.poll(running).toBeGreaterThan(0)
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await expect.poll(running).toBe(0)
})

test('the sign-in card stays readable on top of the posters', async ({ page }) => {
  await page.goto('/')
  const card = page.locator('h1').locator('xpath=ancestor::div[1]')
  const [bg, fg] = await card.evaluate((el) => {
    const s = getComputedStyle(el)
    return [s.backgroundColor, getComputedStyle(el.querySelector('h1')!).color]
  })
  expect(bg).not.toBe('rgba(0, 0, 0, 0)')
  expect(bg).not.toBe(fg)
})
