import { defineConfig, devices } from '@playwright/test'

// Browser tests run the real frontend against a throwaway backend (backend/e2e/server.py: its own database,
// in-memory fake providers). Nothing here can reach a real account. Run: `npm run e2e` (Postgres must be up).
const API = 'http://localhost:8100'
const WEB = 'http://localhost:5180'

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1, // the fake providers are shared state
  retries: process.env.CI ? 1 : 0,
  reporter: [['list']],
  use: { baseURL: WEB, trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  projects: [
    // The installed Chrome is used (no browser download).
    { name: 'desktop', use: { ...devices['Desktop Chrome'], channel: 'chrome', colorScheme: 'light' } },
    { name: 'desktop-dark', use: { ...devices['Desktop Chrome'], channel: 'chrome', colorScheme: 'dark' } },
    { name: 'phone', use: { ...devices['Pixel 7'], channel: 'chrome', colorScheme: 'light' } },
    { name: 'phone-dark', use: { ...devices['Pixel 7'], channel: 'chrome', colorScheme: 'dark' } },
  ],
  webServer: [
    {
      command: 'uv run python -m e2e.server',
      cwd: '../backend',
      url: `${API}/api/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      stdout: 'ignore',
    },
    {
      command: 'npm run dev -- --port 5180 --strictPort',
      env: { API_URL: API },
      url: WEB,
      reuseExistingServer: !process.env.CI,
      stdout: 'ignore',
    },
  ],
})
