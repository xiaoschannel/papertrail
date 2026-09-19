import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * Two environments that never share a port, so both can run at once:
 *   live     — the real archive:  API 8000, dev server 5173  (`npm run dev`)
 *   sandbox  — a throwaway archive with fake models, started by tools/sandbox_server.py:
 *              API 8001, dev server 5174                     (`npm run dev:sandbox`)
 * /api is proxied to that environment's API so the browser sees one origin (no CORS config needed).
 */
export default defineConfig(({ mode }) => {
  const sandbox = mode === 'sandbox'
  return {
    plugins: [react()],
    server: {
      // Explicit IPv4 loopback: on Windows + Node 22 the default 'localhost' binds
      // only ::1, so http://127.0.0.1:5173 is refused. Browsers still reach
      // http://localhost:5173 via fallback, and nothing is exposed to the LAN.
      host: '127.0.0.1',
      port: sandbox ? 5174 : 5173,
      strictPort: true,
      proxy: {
        '/api': { target: `http://127.0.0.1:${sandbox ? 8001 : 8000}`, changeOrigin: true },
      },
    },
  }
})
