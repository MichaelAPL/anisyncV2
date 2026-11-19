import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// The API runs on :8000 in development. OAuth redirects come back to :5173, so /auth is proxied as well.
const api = { target: 'http://localhost:8000', changeOrigin: false }

export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': api, '/auth': api } },
  test: { environment: 'jsdom', setupFiles: ['./src/test/setup.ts'], css: false, restoreMocks: true },
})
