/* Runs the TTS server (apps/tts) next to the engine, so the tts module's Restart can bring it back.

   It starts when the engine starts (if the tts module is on) and stops with it, since the model holds
   GPU memory. A server that's already up on TTS_URL (tools/_run_tts_api.cmd) is used as is and left
   alone. When TTS_URL points at another machine, nothing is started here.
*/
import { spawn } from 'child_process'
import { existsSync } from 'fs'
import { mkdir, readdir, writeFile } from 'fs/promises'
import { extname, join, parse } from 'path'
import axios from 'axios'
import { addLog } from './db.js'
import { kill } from './engine.js'
import { appDir } from './python.js'
import { childEnv, setting } from './settings.js'

let child = null
let stopping = false
let starting = null // the launch in progress, so overlapping starts don't spawn twice

const url = () => new URL(setting('TTS_URL') || 'http://127.0.0.1:8000')
const local = () => ['127.0.0.1', 'localhost'].includes(url().hostname)

// The TTS app has its own venv: its torch and transformers versions clash with the repo's .venv
function ttsPython() {
  const dir = join(appDir('tts'), 'tts_api')
  return process.platform === 'win32'
    ? join(dir, 'Scripts', 'python.exe')
    : join(dir, 'bin', 'python')
}

async function running() {
  try {
    const r = await axios.get(new URL('/api/v1/health', url()).href, { timeout: 800 })
    return r.data?.status === 'ok'
  } catch {
    return false
  }
}

export function startTTS() {
  starting ||= launch().finally(() => (starting = null))
  return starting
}

async function launch() {
  if (child || !local()) return
  if (await running()) {
    console.log(`[tts] using the server already on ${url().origin}`)
    return
  }
  const py = ttsPython()
  if (!existsSync(py)) {
    addLog('error', `Can't start the TTS server: no Python at ${py}`)
    return
  }
  stopping = false
  const proc = spawn(
    py,
    ['-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', url().port || '80'],
    {
      cwd: appDir('tts'),
      env: childEnv({ PYTHONUNBUFFERED: '1' }),
      windowsHide: true
    }
  )
  child = proc
  proc.stdout.on('data', (d) => process.stdout.write(`[tts] ${d}`))
  proc.stderr.on('data', (d) => process.stdout.write(`[tts] ${d}`))
  proc.on('error', (e) => addLog('error', `Could not start the TTS server: ${e.message}`))
  proc.on('exit', (code) => {
    if (!stopping) addLog('error', `TTS server exited on its own (code ${code})`)
    if (child === proc) child = null
  })
}

export function stopTTS() {
  const proc = child
  if (!proc) return Promise.resolve()
  stopping = true
  return new Promise((resolve) => {
    proc.once('exit', resolve)
    kill(proc.pid)
  })
}

export async function restartTTS() {
  await starting
  if (!child && local() && (await running())) {
    throw new Error(
      'The TTS server was started outside the app (tools/_run_tts_api.cmd). Restart it there.'
    )
  }
  await stopTTS()
  await startTTS()
}

// ── Character voices ─────────────────────────────────────────────────────────
/* A character speaks as a reference clip in apps/tts/voices (characters.voice.file on the NAS), or
   the built-in voice when it has none. The engine sends the clip's name with every reply; loadVoice
   gets the server speaking as it right away, so playing a card doesn't leave the first reply waiting
   on it to be encoded. */
const VOICE_TYPES = ['.wav', '.flac', '.mp3', '.ogg']
const voicesDir = () => join(appDir('tts'), 'voices')

/** The clips characters can pick, by file name. */
export async function listVoices() {
  const files = await readdir(voicesDir()).catch(() => [])
  return files
    .filter((f) => VOICE_TYPES.includes(extname(f).toLowerCase()))
    .sort((a, b) => a.localeCompare(b))
}

/** Saves an imported clip (name: its original file name, bytes: its contents) and returns the file
    name it was saved as. An existing clip with the same name is kept, the new one gets a number. */
export async function importVoice(name, bytes) {
  const { name: stem, ext } = parse(String(name))
  if (!VOICE_TYPES.includes(ext.toLowerCase()))
    throw new Error(`Voice clips have to be ${VOICE_TYPES.join(', ')}`)
  const base = stem.replace(/[^\w.-]+/g, '-').replace(/^[-.]+|-+$/g, '') || 'voice'
  await mkdir(voicesDir(), { recursive: true })
  const taken = new Set((await readdir(voicesDir())).map((f) => f.toLowerCase()))
  let file = `${base}${ext.toLowerCase()}`
  for (let i = 2; taken.has(file.toLowerCase()); i++) file = `${base}-${i}${ext.toLowerCase()}`
  await writeFile(join(voicesDir(), file), Buffer.from(bytes))
  return file
}

/** Has the TTS server speak as `voice` ({ engine, file }, or a Chatterbox clip name) from now on.
    { loaded: false } when the server isn't up: the engine loads it once it is. Throws when the
    server can't use it. */
export async function loadVoice(voice) {
  const { engine = 'chatterbox', file = '' } =
    typeof voice === 'string' ? { file: voice } : voice || {}
  await starting
  try {
    await axios.post(new URL('/api/v1/tts/voice', url()).href, null, {
      params: { file: file || '', engine },
      timeout: 60000 // waits for a reply in progress, then encodes the clip or loads Kokoro
    })
    return { loaded: true }
  } catch (e) {
    if (!e.response) return { loaded: false }
    throw new Error(e.response.data?.detail || `The TTS server couldn't load ${file}`)
  }
}
