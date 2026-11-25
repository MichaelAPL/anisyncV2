import AxeBuilder from '@axe-core/playwright'
import type { Page } from '@playwright/test'
import { entries, expect, finishSetup, reset, seed, seedConflicts, signIn, test } from './support'

test.beforeEach(async ({ request }) => reset(request))

// Serious and critical findings only (contrast, names for buttons and fields, landmarks...). The project decides
// light or dark and phone or desktop.
async function violations(page: Page) {
  await page.waitForLoadState('networkidle')
  const result = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze()
  return result.violations
    .filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .map((v) => `${v.id} (${v.impact}): ${v.help} — ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(' | ')}`)
}

test('public pages have no serious accessibility problems', async ({ page }) => {
  const found: string[] = []
  for (const path of ['/', '/privacy']) {
    await page.goto(path)
    found.push(...(await violations(page)).map((v) => `${path}: ${v}`))
  }
  expect(found).toEqual([])
})

// One test per page: all nine pages in a single test could pass the 60 s limit on a slow run (2 of 6 runs did), and
// a failure now names the page. Each test seeds its own user, because the database is reset before every test.
const SIGNED_IN_PAGES = [
  '/dashboard',
  '/list',
  '/settings',
  '/preview',
  '/link',
  '/admin',
  '/admin/users',
  '/admin/titles',
  '/admin/site',
]

for (const path of SIGNED_IN_PAGES) {
  test(`signed-in page ${path} has no serious accessibility problems`, async ({ page, context, request }) => {
    const token = await seed(request, {
      name: 'a11y',
      role: 'admin',
      accounts: ['mal', 'anilist', 'kitsu'],
      lists: { mal: entries(12, (n) => ({ total: 12, adult: n === 5, score: 70 })) },
    })
    await signIn(context, token)
    await finishSetup(page)
    await seedConflicts(request)
    await page.goto(path)
    expect(await violations(page)).toEqual([])
  })
}
