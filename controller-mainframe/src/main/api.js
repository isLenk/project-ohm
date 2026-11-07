import express from 'express'
import { existsSync, readdirSync, readFileSync } from 'fs'
import { join } from 'path'
import { addEvent, addLog } from './db.js'
import { engineStatus, heard, onEngineChange } from './engine.js'
import { nas } from './nas.js'
import { repoRoot } from './python.js'
import { saveSettings, setting } from './settings.js'

const api = express()
api.use(express.json())

/* Modules: one per folder in modules/ that has a body.json ({ name, description, required }).
   They're read from the folders so the app knows them while main.py is off; main.py's heartbeat
   then fills in how each one is doing:
     ok: true (working) | false (failing) | null (off, or main.py isn't running)
*/
export const modules = {}
const restarts = new Set() // modules to restart, handed to main.py with the next heartbeat

function loadModules() {
  const dir = join(repoRoot(), 'modules')
  if (!existsSync(dir)) return
  for (const folder of readdirSync(dir)) {
    const body = join(dir, folder, 'body.json')
    if (!existsSync(body)) continue
    try {
      const {
        name = folder,
        description = '',
        required = false
      } = JSON.parse(readFileSync(body, 'utf8'))
      modules[name] = { name, description, required, ok: null, detail: '', since: null }
    } catch (e) {
      console.error(`Could not read ${body}:`, e.message)
    }
  }
}
loadModules()

const disabled = () =>
  new Set(
    setting('OHM_DISABLED_MODULES')
      .split(',')
      .map((name) => name.trim())
      .filter(Boolean)
  )

export function listModules() {
  const off = disabled()
  return Object.values(modules).map((m) => ({
    ...m,
    enabled: m.required || !off.has(m.name),
    restarting: restarts.has(m.name)
  }))
}

/** Saved now; main.py reads it when it starts, so a running engine needs a restart to apply it. */
export function setModuleEnabled(name, on) {
  const m = modules[name]
  if (!m) throw new Error(`No module called ${name}`)
  if (m.required) throw new Error(`${name} is required and always runs.`)
  const off = disabled()
  if (on) off.delete(name)
  else off.add(name)
  saveSettings({ OHM_DISABLED_MODULES: [...off].join(',') })
  addLog('update', `${name} turned ${on ? 'on' : 'off'}`)
  return listModules()
}

export function restartModule(name) {
  if (!modules[name]) throw new Error(`No module called ${name}`)
  if (engineStatus().state !== 'running') throw new Error('Start the engine first.')
  restarts.add(name)
  addEvent('module', name, 'restart')
  return listModules()
}

function setHealth(m, ok, detail) {
  if (ok !== m.ok) {
    addEvent('module', m.name, ok === null ? 'off' : ok ? 'up' : 'down')
    if (ok === false) addLog('error', `${m.name}: ${detail}`)
    m.since = new Date()
  }
  m.ok = ok
  m.detail = detail
}

// Nothing is running while main.py is stopped
onEngineChange(({ state }) => {
  if (state !== 'stopped') return
  restarts.clear()
  for (const m of Object.values(modules)) setHealth(m, null, '')
})

/* Every few seconds from main.py (pyhandler.start_heartbeat)
    POST /api/modules/heartbeat
    body { pid, modules: { name: { ok, detail, off? } } }
    → { restart: [names] }
*/
api.post('/api/modules/heartbeat', (req, res) => {
  const { pid, modules: health = {} } = req.body
  heard(pid)
  for (const [name, h] of Object.entries(health)) {
    modules[name] ||= { name, description: '', required: false, ok: null, detail: '', since: null }
    setHealth(modules[name], h.off ? null : Boolean(h.ok), h.detail || '')
  }
  const restart = [...restarts]
  restarts.clear()
  res.json({ restart })
})

/* Module
    POST /api/modules
    body {
      name: string,
      description: string,
      required: boolean,
    }
*/
api.post('/api/modules', (req, res) => {
  const { name, description, required } = req.body
  const known = Boolean(modules[name])
  modules[name] = {
    ok: null,
    detail: '',
    since: null,
    ...modules[name],
    name,
    description,
    required
  }
  if (!known) addLog('info', `New Module - ${name} (req=${required})`)
  res.status(201).json({ message: 'Module added successfully' })
})

api.put('/api/modules/:name', (req, res) => {
  const { name } = req.params
  const { description, required } = req.body
  if (modules[name]) {
    modules[name] = { ...modules[name], description, required }
    res.status(200).json({ message: 'Module updated successfully' })
  } else {
    res.status(404).json({ message: 'Module not found' })
  }
})

api.get('/api/modules/', (req, res) => {
  const off = disabled()
  const missing = Object.values(modules).filter((m) => m.required && m.ok !== true)
  if (missing.length > 0) {
    return res.status(400).json({
      ready: false,
      message: 'Missing required modules',
      missingRequiredModules: missing.map((m) => m.name)
    })
  }
  return res.status(200).json({ ready: true, modules, disabled: [...off] })
})

api.post('/api/send', (req, res) => {
  const { character, input } = req.body
  console.log(`Received input for character ${character}: ${input}`)

  return res.status(200).json({
    message: 'OK'
  })
})

/* Search memories for a character.
    POST /api/:character/memories?input=...
    The bot talks to the NAS directly now; this route is kept for the UI and older callers.
*/
api.post('/api/:character/memories', async (req, res) => {
  const { character } = req.params
  const { input } = req.query

  if (!character || !input) {
    return res.status(400).json({ message: 'Character and input are required' })
  }
  const memoryApi = nas()
  if (!memoryApi) {
    return res.status(503).json({ message: 'The memory service address is not set' })
  }

  try {
    const { data } = await memoryApi.post('/memories/search', { character, text: String(input) })
    return res.status(200).json({ character, data })
  } catch (error) {
    console.error('Error fetching memories from NAS:', error.message)
    return res.status(502).json({ message: 'Memory service unavailable', character, data: [] })
  }
})

const API_PORT = 3001
api.listen(API_PORT, () => {
  console.log(`API server is running at http://localhost:${API_PORT}`)
})

export default api
