import { entries, expect, finishSetup, overflowsHorizontally, reset, seed, signIn, test } from './support'

test.beforeEach(async ({ request }) => reset(request))

const USER_PAGES = ['/dashboard', '/list', '/settings', '/preview']
const ADMIN_PAGES = ['/admin', '/admin/syncs', '/admin/users', '/admin/titles', '/admin/providers', '/admin/site']
const PUBLIC_PAGES = ['/', '/privacy', '/nowhere']

test('no page scrolls sideways on this screen', async ({ page, context, request }) => {
  const token = await seed(request, {
    name: 'layout',
    role: 'admin',
    accounts: ['mal', 'anilist', 'kitsu'],
    lists: { mal: entries(30, (n) => ({ total: 12, adult: n % 9 === 0, title: `A fairly long title number ${n} with several words in it` })) },
  })
  await signIn(context, token)
  await finishSetup(page)
  const problems: string[] = []
  for (const path of [...PUBLIC_PAGES, ...USER_PAGES, ...ADMIN_PAGES]) {
    await page.goto(path)
    await page.waitForLoadState('networkidle')
    const result = await overflowsHorizontally(page)
    if (result.over) problems.push(`${path}: content is ${result.width}px wide on a ${result.viewport}px screen`)
  }
  expect(problems).toEqual([])
})

test('the link and master screens fit too', async ({ page, context, request }) => {
  const token = await seed(request, { name: 'layout2', accounts: ['mal', 'anilist'], lists: { mal: entries(3) } })
  await signIn(context, token)
  for (const path of ['/link', '/master']) {
    await page.goto(path)
    await page.waitForLoadState('networkidle')
    expect(await overflowsHorizontally(page), path).toMatchObject({ over: false })
  }
})
