import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev: proxy /api to the FastAPI backend so the browser sees one origin
// (no CORS config needed). Serving the built bundle from FastAPI comes later.
export default defineConfig({
  plugins: [react()],
  server: {
    // Explicit IPv4 loopback: on Windows + Node 22 the default 'localhost' binds
    // only ::1, so http://127.0.0.1:5173 is refused. Browsers still reach
    // http://localhost:5173 via fallback, and nothing is exposed to the LAN.
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})
