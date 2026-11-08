/* Starts curator_service (the Conversation Curator backend) next to the app and stops it on quit.

   The service only answers requests that carry the token it was started with. The token is kept
   in the app's data folder, so after a restart (or an electron-vite reload that left the old
   service running) the app can keep using the service it already started. A service that turns
   the token down is no use to the app, so it gets replaced. Training runs live in their own
   process and the new service picks them back up (train/runs.py resume).
*/
import { spawn } from 'child_process'
import { randomBytes } from 'crypto'
import { existsSync, readFileSync, writeFileSync } from 'fs'
import { join } from 'path'
import { app } from 'electron'
import axios from 'axios'
import { appDir, python } from './python.js'
import { BRAIN_KEYS, childEnv, setting } from './settings.js'

const PORT = Number(setting('CURATOR_PORT') || 8200)
const URL = `http://127.0.0.1:${PORT}`

let child = null
let token = ''
let ready = Promise.resolve()

const tokenFile = () => join(app.getPath('userData'), 'curator-token')

function savedToken() {
  try {
    return existsSync(tokenFile()) ? readFileSync(tokenFile(), 'utf8').trim() : ''
  } catch {
    return ''
  }
}

/** The running service's /health, or null when nothing answers. */
async function health() {
  try {
    // It asks the GPU and llama-server, which takes a couple of seconds. Giving up sooner would
    // start a second service that can't get the port, with a token the running one doesn't know.
    // Nothing listening fails at once, so the long wait only applies when something is there.
    const r = await axios.get(`${URL}/health`, { timeout: 5000 })
    return r.data?.ok === true ? r.data : null
  } catch {
    return null
  }
}

/** Whether the running service takes requests with this token (or with none). */
async function accepts(t) {
  try {
    await axios.get(`${URL}/jobs`, { timeout: 2000, headers: { 'X-Curator-Token': t } })
    return true
  } catch (e) {
    // Only a token refusal means it's unusable; anything else, leave it be
    return e.response?.status !== 401
  }
}

async function stopService(pid) {
  try {
    process.kill(pid)
  } catch (e) {
    throw new Error(`could not stop the old service (pid ${pid}): ${e.message}`)
  }
  for (let i = 0; i < 50 && (await health()); i++) await new Promise((r) => setTimeout(r, 100))
}

async function stopStale(pid) {
  if (!pid)
    throw new Error(`a service on ${URL} rejects the app's token; stop it and restart the app`)
  console.log(`[curator] the service on ${URL} rejects the app's token; restarting it (pid ${pid})`)
  await stopService(pid)
}

export function startCurator() {
  ready = launch().catch((e) => console.error(`[curator] ${e.message}`))
  return ready
}

// The settings the service reads. They reach it as its environment, so changing one means a restart.
// (CURATOR_PORT is left out: the app only picks a new port up when it restarts too.)
export const CURATOR_SETTINGS = [
  'MEMORY_API_URL',
  'MEMORY_API_KEY',
  'LLAMA_CPP_DIR',
  'CURATOR_MODELS_DIR',
  'CURATOR_DATA_DIR',
  'CURATOR_LLM_URL',
  'CURATOR_PYTHON',
  'CURATOR_TRAIN_VRAM_GB',
  'CURATOR_TRAIN_SIMULATE',
  // The Curator calls the brain too (curating, judging, labelling)
  ...BRAIN_KEYS
]

/** Replace the running service with a new one that has the current settings. Training runs carry on
 *  in their own process and the new service picks them back up. */
export async function restartCurator() {
  await ready
  const running = await health()
  if (running && !running.pid)
    throw new Error(`the service on ${URL} is too old to say its pid; stop it and restart the app`)
  console.log('[curator] settings changed; restarting the service')
  if (running) await stopService(running.pid)
  if (child) child.kill() // the venv's python.exe launcher, if it outlived the interpreter
  return startCurator()
}

async function launch() {
  const running = await health()
  if (running) {
    const saved = savedToken()
    if (await accepts(saved)) {
      token = saved
      console.log(`[curator] using the service already on ${URL}`)
      return
    }
    await stopStale(running.pid)
  }
  token = randomBytes(24).toString('hex')
  writeFileSync(tokenFile(), token)
  const py = python()
  child = spawn(
    py,
    [
      '-m',
      'uvicorn',
      'curator_service.app.main:app',
      '--host',
      '127.0.0.1',
      '--port',
      String(PORT)
    ],
    {
      cwd: appDir('curator'),
      env: childEnv({ CURATOR_TOKEN: token, PYTHONUNBUFFERED: '1' }),
      windowsHide: true
    }
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
