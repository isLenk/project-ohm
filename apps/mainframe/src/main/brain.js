/* The cloud "brains" Settings › Integrations sets up: Codex and Claude Code (the owner's
   subscriptions, signed in through their own CLIs) and the API-key backends.

   `python -m brain status` is the brain package's own view of what's installed, signed in and
   spent today, so the page asks it rather than guessing from here. Install and Sign in only ever
   run the fixed commands below; nothing the renderer sends ends up in a command line.
*/
import { execFile, spawn } from 'child_process'
import { python, repoRoot } from './python.js'
import { childEnv } from './settings.js'

const TOOLS = {
  codex: { pkg: '@openai/codex', login: 'codex login', title: 'Codex sign-in' },
  claude: { pkg: '@anthropic-ai/claude-code', login: 'claude', title: 'Claude Code sign-in' }
}

const tool = (name) => {
  if (!TOOLS[name]) throw new Error(`Unknown tool: ${name}`)
  return TOOLS[name]
}

/** What the brain package says about every backend, or {error} when it can't say. */
export function brainStatus() {
  return new Promise((resolve) => {
    execFile(
      python(),
      ['-m', 'brain', 'status'],
      { cwd: repoRoot(), env: childEnv(), timeout: 20000, windowsHide: true },
      (err, stdout, stderr) => {
        if (err) {
          // The last line of stderr is usually the actual reason (a traceback ends with it)
          const why = String(stderr).trim().split('\n').pop() || err.message
          return resolve({ error: why })
        }
        try {
          resolve(JSON.parse(stdout))
        } catch {
          resolve({
            error: `brain status printed something that isn't JSON: ${String(stdout).slice(0, 200)}`
          })
        }
      }
    )
  })
}

/** `npm install -g` the tool. Each output line goes to onLine as it comes, since this takes a minute. */
export function installTool(name, onLine = () => {}) {
  const { pkg } = tool(name)
  return new Promise((resolve) => {
    // npm is npm.cmd on Windows, which needs a shell to run. The arguments are fixed, so that's safe.
    const child = spawn('npm', ['install', '-g', pkg], {
      env: childEnv(),
      shell: process.platform === 'win32',
      windowsHide: true
    })
    let output = ''
    const take = (chunk) => {
      output += chunk
      for (const line of String(chunk).split(/\r?\n/)) if (line.trim()) onLine(line.trim())
    }
    child.stdout.on('data', take)
    child.stderr.on('data', take)
    child.on('error', (e) => resolve({ ok: false, output: `Could not run npm: ${e.message}` }))
    child.on('exit', (code) => resolve({ ok: code === 0, code, output: output.slice(-4000) }))
  })
}

/** Open a terminal running the tool's sign-in, so its browser flow can happen. It stays open
 *  afterwards so the owner can see how it went. */
export function openSignIn(name) {
  const { login, title } = tool(name)
  if (process.platform === 'win32') {
    // Verbatim so the quoted window title reaches `start` as is; Node would escape the quotes.
    spawn('cmd', ['/c', 'start', `"${title}"`, 'cmd', '/k', login], {
      detached: true,
      shell: false,
      windowsVerbatimArguments: true,
      stdio: 'ignore'
    }).unref()
  } else if (process.platform === 'darwin') {
    spawn('osascript', ['-e', `tell application "Terminal" to do script "${login}"`], {
      detached: true,
      stdio: 'ignore'
    }).unref()
  } else {
    spawn('x-terminal-emulator', ['-e', `sh -c '${login}; exec sh'`], {
      detached: true,
      stdio: 'ignore'
    }).unref()
  }
  return { ok: true }
}
