// Starts curator_service (the Conversation Curator backend) next to the app and stops it on quit.
// Python is found in this order: CURATOR_PYTHON, the repo's venv (Scripts\python.exe or bin/python), python.
import { app } from 'electron'
import { spawn } from 'child_process'
import { existsSync } from 'fs'
import { join, resolve } from 'path'
import { randomBytes } from 'crypto'
import axios from 'axios'

const PORT = Number(process.env.CURATOR_PORT || 8200)
const URL = `http://127.0.0.1:${PORT}`

let child = null
let token = ''
let ready = Promise.resolve()

function repoRoot() {
  return process.env.CURATOR_REPO || resolve(app.getAppPath(), '..')
}

function python(root) {
  if (process.env.CURATOR_PYTHON) return process.env.CURATOR_PYTHON
  const candidates =
    process.platform === 'win32'
      ? [join(root, 'Scripts', 'python.exe')]
      : [join(root, 'bin', 'python'), join(root, '.venv', 'bin', 'python')]
  return candidates.find((p) => existsSync(p)) || (process.platform === 'win32' ? 'python' : 'python3')
}

async function alreadyRunning() {
  try {
    const r = await axios.get(`${URL}/health`, { timeout: 800 })
    return r.data?.ok === true
  } catch {
    return false
  }
}

export function startCurator() {
  ready = launch()
  return ready
}

async function launch() {
  if (await alreadyRunning()) {
    console.log(`[curator] using the service already on ${URL}`)
    return
  }
  const root = repoRoot()
  token = randomBytes(24).toString('hex')
  const py = python(root)
  child = spawn(
    py,
    ['-m', 'uvicorn', 'curator_service.app.main:app', '--host', '127.0.0.1', '--port', String(PORT)],
    { cwd: root, env: { ...process.env, CURATOR_TOKEN: token, PYTHONUNBUFFERED: '1' }, windowsHide: true }
  )
  child.stdout.on('data', (d) => process.stdout.write(`[curator] ${d}`))
  child.stderr.on('data', (d) => process.stdout.write(`[curator] ${d}`))
  child.on('exit', (code) => {
    console.log(`[curator] service exited (${code})`)
    child = null
  })
  child.on('error', (e) => console.error(`[curator] could not start ${py}:`, e.message))
}

export function stopCurator() {
  if (child) child.kill()
}

export async function curatorConfig() {
  await ready
  return { url: URL, token }
}
