// Writes the API's OpenAPI schema (frontend/openapi.json) straight from the backend, so the TypeScript
// types generated from it (`npm run gen:api`) always match the API as it is. Needs the backend's Python
// environment: $PYTHON if set, else the nearest .venv at or above the repo (a worktree under
// .claude/worktrees/ uses the main checkout's), else `python` on the PATH.
import { execFileSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const repo = dirname(dirname(dirname(fileURLToPath(import.meta.url))))

function nearestVenvPython() {
  for (let folder = repo; ; folder = dirname(folder)) {
    const found = [join(folder, '.venv', 'Scripts', 'python.exe'), join(folder, '.venv', 'bin', 'python')].find(existsSync)
    if (found || dirname(folder) === folder) return found
  }
}

const python = process.env.PYTHON || nearestVenvPython() || 'python'
execFileSync(python, [join(repo, 'tools', 'export_openapi.py')], { cwd: repo, stdio: 'inherit' })
