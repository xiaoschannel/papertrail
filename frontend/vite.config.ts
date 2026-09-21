import { readFileSync, statSync } from 'node:fs'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * Environments that never share a port, so they can all run at once (the scheme is in dev_ports.py):
 *   live     — the real archive:  API 8000, dev server 5173  (`npm run dev`), main checkout only
 *   sandbox  — a throwaway archive with fake models, started by tools/sandbox_server.py
 *              (`npm run dev:sandbox`): API 8001, dev server 5174 in the main checkout; in a worktree,
 *              the slot tools/worktree_setup.py claimed for it, read from .dev-ports.json
 * /api is proxied to that environment's API so the browser sees one origin (no CORS config needed).
 */
const checkout = new URL('../', import.meta.url)

/** A worktree's .git is a file pointing into the main checkout's; the main checkout's is a folder. */
function isWorktree(): boolean {
  try {
    return statSync(new URL('.git', checkout)).isFile()
  } catch {
    return false
  }
}

function sandboxPorts(): { api: number; web: number } {
  if (!isWorktree()) return { api: 8001, web: 5174 }
  let claimed: { sandbox_api: number; sandbox_web: number }
  try {
    claimed = JSON.parse(readFileSync(new URL('.dev-ports.json', checkout), 'utf8'))
  } catch {
    // Not the main checkout's 5174: that would serve this checkout's code against another's API.
    throw new Error("This worktree has no ports of its own yet — run: python tools/worktree_setup.py (with the main checkout's .venv)")
  }
  return { api: claimed.sandbox_api, web: claimed.sandbox_web }
}

export default defineConfig(({ command, mode, isPreview }) => {
  const sandbox = mode === 'sandbox'
  // Ports matter only to the dev server; a build or typecheck in a worktree needs no setup.
  const devServer = command === 'serve' && !isPreview
  if (devServer && !sandbox && isWorktree()) {
    throw new Error('A worktree runs the sandbox only (npm run dev:sandbox): the live archive is the main checkout\'s.')
  }
  const ports = devServer && sandbox ? sandboxPorts() : { api: 8000, web: 5173 }
  return {
    plugins: [react()],
    server: {
      // Explicit IPv4 loopback: on Windows + Node 22 the default 'localhost' binds
      // only ::1, so http://127.0.0.1:5173 is refused. Browsers still reach
      // http://localhost:5173 via fallback, and nothing is exposed to the LAN.
      host: '127.0.0.1',
      port: ports.web,
      strictPort: true,
      proxy: {
        '/api': { target: `http://127.0.0.1:${ports.api}`, changeOrigin: true },
      },
    },
  }
})
