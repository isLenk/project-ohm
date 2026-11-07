/* Runs the Python side (main.py: Discord and the modules) as a child of the app.

   Start/Stop/Restart come from the Engine panel. A main.py started by hand
   (executables/_run_script.cmd) is noticed through its heartbeat and shown as running, and
   Stop ends it too, though that script starts it again on its own.
   Every start, stop and crash goes into events.db, which is where uptime comes from.
*/
import { execFile, spawn } from 'child_process'
import { addEvent, addLog } from './db.js'
import { python, repoRoot } from './python.js'
import { childEnv } from './settings.js'

const HEARTBEAT_TIMEOUT = 20000 // ms of silence before a main.py we didn't start counts as gone
const OUTPUT_LINES = 300

let child = null
let state = 'stopped' // stopped | starting | running | stopping
let pid = null
let startedAt = null
let lastBeat = 0
let stopping = false
let silent = false // already logged that our main.py stopped answering
const output = []
const listeners = new Set()

export function onEngineChange(fn) {
  listeners.add(fn)
}

function changed() {
  const status = engineStatus()
  for (const fn of listeners) fn(status)
}

function set(next, event) {
  state = next
  if (event) addEvent('engine', 'python', event)
  changed()
}

export function engineStatus() {
  return {
    state,
    pid,
    startedAt,
    external: state !== 'stopped' && !child,
    lastBeat: lastBeat || null
  }
}

export const engineOutput = () => output.join('')

function keep(chunk) {
  process.stdout.write(`[engine] ${chunk}`)
  output.push(String(chunk))
  if (output.length > OUTPUT_LINES) output.splice(0, output.length - OUTPUT_LINES)
}

/** Called for every heartbeat main.py sends (see pyhandler.start_heartbeat). */
export function heard(beatPid) {
  lastBeat = Date.now()
  silent = false
  if (state === 'running' || state === 'stopping') return
  if (!child) {
    // Started outside the app
    pid = beatPid
    startedAt = new Date()
    return set('running', 'start')
  }
  set('running')
}

export function startEngine() {
  if (state !== 'stopped') return engineStatus()
  const root = repoRoot()
  const py = python(root)
  stopping = false
  child = spawn(py, ['main.py'], {
    cwd: root,
    env: childEnv({ PYTHONUNBUFFERED: '1' }),
    windowsHide: true
  })
  pid = child.pid
  startedAt = new Date()
  child.stdout.on('data', keep)
  child.stderr.on('data', keep)
  child.on('error', (e) => {
    addLog('error', `Could not start ${py} main.py: ${e.message}`)
    if (!pid) {
      // Never started, so there won't be an exit event
      child = null
      startedAt = null
      set('stopped')
    }
  })
  child.on('exit', (code) => {
    const crashed = !stopping
    if (crashed) addLog('error', `main.py exited on its own (code ${code})`)
    child = null
    pid = null
    startedAt = null
    set('stopped', crashed ? 'crash' : 'stop')
  })
  set('starting', 'start')
  return engineStatus()
}

function kill(target) {
  if (process.platform === 'win32') {
    // /t takes the whole tree: the venv's python.exe is a launcher that runs the real one as a child
    execFile('taskkill', ['/pid', String(target), '/t', '/f'], () => {})
  } else {
    try {
      process.kill(target, 'SIGTERM')
    } catch {
      // already gone
    }
  }
}

export function stopEngine() {
  if (state === 'stopped' || !pid) return Promise.resolve(engineStatus())
  stopping = true
  const target = pid
  const ours = child
  set('stopping')
  return new Promise((resolve) => {
    if (ours) {
      ours.once('exit', () => resolve(engineStatus()))
    } else {
      setTimeout(() => {
        pid = null
        startedAt = null
        lastBeat = 0
        set('stopped', 'stop')
        resolve(engineStatus())
      }, 1500)
    }
    kill(target)
  })
}

export async function restartEngine() {
  await stopEngine()
  addEvent('engine', 'python', 'restart')
  return startEngine()
}

// A main.py we didn't start has no exit event, so notice when its heartbeat stops instead.
// Ours gets the same treatment once it's running, in case it hangs instead of exiting.
setInterval(() => {
  if (state === 'running' && Date.now() - lastBeat > HEARTBEAT_TIMEOUT) {
    if (child) {
      if (!silent) addLog('error', 'main.py stopped answering')
      silent = true
      return
    }
    pid = null
    startedAt = null
    set('stopped', 'crash')
  }
}, 5000)
