import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// The API runs on :8000 in development (API_URL overrides it for the browser tests). OAuth redirects come back to
// the dev server, so /auth is proxied as well.
const api = { target: process.env.API_URL ?? 'http://localhost:8000', changeOrigin: false }

export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': api, '/auth': api } },
  test: { exclude: ['e2e/**', 'node_modules/**'], environment: 'jsdom', setupFiles: ['./src/test/setup.ts'], css: false, restoreMocks: true },
})
