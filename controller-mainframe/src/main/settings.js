/* Settings from the Settings page, kept in settings.json in the app's data folder.

   Every setting is named after the environment variable the Python side already reads, and the
   whole set is passed as environment to main.py and curator_service when they start. A variable
   that's already set in the environment wins and shows as locked on the Settings page.
*/
import { app } from 'electron'
import { existsSync, readFileSync, writeFileSync } from 'fs'
import { join } from 'path'

// The keys the Settings page edits. Anything else in settings.json is ignored.
// (DATABASE_URL, EMBED_MODEL and MODEL_CACHE belong to the memory service's .env on the NAS.)
export const KEYS = [
  'LLM_URL',
  'LLAMA_CPP_DIR',
  'CURATOR_MODELS_DIR',
  'DISCORD_TOKEN',
  'TTS_URL',
  'MEMORY_API_URL',
  'MEMORY_API_KEY',
  'CURATOR_PORT',
  'CURATOR_DATA_DIR',
  'CURATOR_LLM_URL',
  'CURATOR_PYTHON',
  'CURATOR_TRAIN_VRAM_GB',
  'CURATOR_TRAIN_SIMULATE',
  'OHM_DISABLED_MODULES'
]

// Values the build was configured with (controller-mainframe/.env), used when nothing else is set.
const BUILT_IN = {
  MEMORY_API_URL: import.meta.env.MAIN_VITE_MEMORY_API_URL,
  MEMORY_API_KEY: import.meta.env.MAIN_VITE_MEMORY_API_KEY
}

const file = () => join(app.getPath('userData'), 'settings.json')
let saved = null

function load() {
  if (saved) return saved
  try {
    saved = existsSync(file()) ? JSON.parse(readFileSync(file(), 'utf8')) : {}
  } catch (e) {
    console.error('Could not read settings.json, starting empty:', e.message)
    saved = {}
  }
  return saved
}

/** One setting: the environment, then settings.json, then what the build had. */
export function setting(key) {
  return process.env[key] || load()[key] || BUILT_IN[key] || ''
}

/** Everything the Settings page needs: values, and which ones the environment locks. */
export function getSettings() {
  const values = {}
  for (const key of KEYS) values[key] = setting(key)
  return { values, locked: KEYS.filter((key) => process.env[key]), file: file() }
}

export function saveSettings(values) {
  const next = { ...load() }
  for (const key of KEYS) {
    if (key in values && !process.env[key]) next[key] = String(values[key] ?? '').trim()
  }
  writeFileSync(file(), JSON.stringify(next, null, 2))
  saved = next
  return getSettings()
}

/** Environment for a child process: the saved settings under whatever is already set. */
export function childEnv(extra = {}) {
  const env = {}
  for (const key of KEYS) {
    const value = setting(key)
    if (value) env[key] = value
  }
  return { ...env, ...process.env, ...extra }
}
