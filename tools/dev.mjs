// Starts the apps for development from the repo root.
//   npm run dev        the Electron app (it starts the curator itself, and the engine from its UI)
//   npm run dev:all    also llama-server and the TTS API, through the same .cmd files you'd double-click
// Ctrl+C stops everything it started.
import { spawn, execFileSync } from 'node:child_process'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const win = process.platform === 'win32'

const apps = {
  mainframe: { cwd: join(root, 'apps', 'mainframe'), cmd: win ? 'npm.cmd' : 'npm', args: ['run', 'dev'] },
  llama: { cwd: join(root, 'tools'), cmd: 'cmd', args: ['/c', '_run_llama.cmd'], winOnly: true },
  tts: { cwd: join(root, 'tools'), cmd: 'cmd', args: ['/c', '_run_tts_api.cmd'], winOnly: true }
}

const wanted = process.argv[2] === 'all' ? ['llama', 'tts', 'mainframe'] : ['mainframe']
const children = []
let stopping = false

function prefix(name, stream, out) {
  let rest = ''
  stream.on('data', (d) => {
    const lines = (rest + d).split(/\r?\n/)
    rest = lines.pop()
    for (const line of lines) out.write(`[${name}] ${line}\n`)
  })
  stream.on('end', () => rest && out.write(`[${name}] ${rest}\n`))
}

for (const name of wanted) {
  const app = apps[name]
  if (app.winOnly && !win) {
    console.log(`[dev] skipping ${name}: its launcher is a Windows .cmd`)
    continue
  }
  // stdin is closed so the `pause` at the end of the .cmd launchers doesn't wait for a key
  const child = spawn(app.cmd, app.args, {
    cwd: app.cwd,
    stdio: ['ignore', 'pipe', 'pipe'],
    shell: win && app.cmd.endsWith('.cmd')
  })
  prefix(name, child.stdout, process.stdout)
  prefix(name, child.stderr, process.stderr)
  child.on('exit', (code) => {
    console.log(`[dev] ${name} exited (${code})`)
    if (name === 'mainframe') stop()
  })
  children.push(child)
}

function stop() {
  if (stopping) return
  stopping = true
  for (const child of children) {
    if (child.exitCode !== null) continue
    try {
      // /t takes the whole tree (cmd -> llama-server, npm -> electron)
      if (win) execFileSync('taskkill', ['/pid', String(child.pid), '/t', '/f'], { stdio: 'ignore' })
      else child.kill('SIGTERM')
    } catch {
      // already gone
    }
  }
  process.exit(0)
}

process.on('SIGINT', stop)
process.on('SIGTERM', stop)
