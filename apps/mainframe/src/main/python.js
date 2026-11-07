// Where the repo is and which Python to run it with, for the processes the app starts.
// Python is found in this order: CURATOR_PYTHON, the repo's venv (Scripts\python.exe or bin/python), python.
import { app } from 'electron'
import { existsSync } from 'fs'
import { join, resolve } from 'path'
import { setting } from './settings.js'

export function repoRoot() {
  return process.env.CURATOR_REPO || resolve(app.getAppPath(), '..', '..')
}

export function python(root = repoRoot()) {
  if (setting('CURATOR_PYTHON')) return setting('CURATOR_PYTHON')
  const candidates =
    process.platform === 'win32'
      ? [join(root, 'Scripts', 'python.exe')]
      : [join(root, 'bin', 'python'), join(root, '.venv', 'bin', 'python')]
  return (
    candidates.find((p) => existsSync(p)) || (process.platform === 'win32' ? 'python' : 'python3')
  )
}
