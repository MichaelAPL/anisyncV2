import { test as base, expect, type APIRequestContext, type BrowserContext, type Page } from '@playwright/test'

export const API = 'http://localhost:8100'
export const WEB = 'http://localhost:5180'

// A 1x1 transparent PNG: covers and posters come from the providers' CDNs, which tests must never depend on.
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==', 'base64')

/** `test` with the outside world stubbed: provider images and Google Fonts. */
export const test = base.extend({
  context: async ({ context }, use) => {
    await context.route(/https:\/\/(s4\.anilist\.co|cdn\.myanimelist\.net|media\.kitsu\.app)\/.*/, (route) =>
      route.fulfill({ status: 200, contentType: 'image/png', body: PNG }),
    )
    await context.route(/https:\/\/fonts\.(googleapis|gstatic)\.com\/.*/, (route) =>
      route.fulfill({ status: 200, contentType: 'text/css', body: '' }),
    )
    await use(context)
  },
})
export { expect }

export interface Entry { n: number; title?: string; status?: string; progress?: number; total?: number | null; score?: number | null; adult?: boolean }
export interface SeedOptions {
  name: string
  master?: string
  accounts?: string[]
  role?: string
  lists?: Record<string, Entry[]>
  hides_adult?: string[]
}

export async function reset(request: APIRequestContext) {
  expect((await request.post(`${API}/__e2e/reset`)).ok()).toBeTruthy()
}

/** Creates a user with accounts and lists on the fake providers; returns the session cookie value. */
export async function seed(request: APIRequestContext, options: SeedOptions): Promise<string> {
  const res = await request.post(`${API}/__e2e/seed`, { data: options })
  expect(res.ok(), await res.text()).toBeTruthy()
  return (await res.json()).session
}

export async function signIn(context: BrowserContext, token: string) {
  await context.addCookies([{ name: 'anisync_session', value: token, url: WEB }])
}

export const entries = (count: number, make: (n: number) => Partial<Entry> = () => ({})): Entry[] =>
  Array.from({ length: count }, (_, i) => ({ n: i + 1, title: `Title ${i + 1}`, status: 'watching', progress: i % 12, ...make(i + 1) }))

export async function remote(request: APIRequestContext, provider: string, name: string) {
  return (await (await request.get(`${API}/__e2e/remote/${provider}/${name}`)).json()) as Record<string, { status: string; progress: number }>
}

/** True when the page scrolls sideways: something is wider than the screen. */
export async function overflowsHorizontally(page: Page): Promise<{ over: boolean; width: number; viewport: number }> {
  return page.evaluate(() => ({
    over: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    width: document.documentElement.scrollWidth,
    viewport: document.documentElement.clientWidth,
  }))
}

/** Finish setup in the browser so the dashboard doesn't send the user to the link page. */
export async function finishSetup(page: Page) {
  await page.addInitScript(() => localStorage.setItem('anisync-setup-done', '1'))
}
