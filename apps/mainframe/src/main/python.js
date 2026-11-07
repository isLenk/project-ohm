// Where the repo is and which Python to run it with, for the processes the app starts.
// Python is found in this order: CURATOR_PYTHON, the repo's .venv (made by `uv sync --all-packages`),
// the old venv in the repo root itself, python.
import { app } from 'electron'
import { existsSync } from 'fs'
import { join, resolve } from 'path'
import { setting } from './settings.js'

export function repoRoot() {
  return process.env.CURATOR_REPO || resolve(app.getAppPath(), '..', '..')
}

// Where each Python app lives in the repo
export const appDir = (name) => join(repoRoot(), 'apps', name)

export function python(root = repoRoot()) {
  if (setting('CURATOR_PYTHON')) return setting('CURATOR_PYTHON')
  const candidates =
    process.platform === 'win32'
      ? [join(root, '.venv', 'Scripts', 'python.exe'), join(root, 'Scripts', 'python.exe')]
      : [join(root, '.venv', 'bin', 'python'), join(root, 'bin', 'python')]
  return (
    candidates.find((p) => existsSync(p)) || (process.platform === 'win32' ? 'python' : 'python3')
  )
}
