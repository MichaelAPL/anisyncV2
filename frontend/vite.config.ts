import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The API runs on :8000 in development. OAuth redirects come back to :5173, so /auth is proxied as well.
const api = { target: 'http://localhost:8000', changeOrigin: false }

export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': api, '/auth': api } },
})
