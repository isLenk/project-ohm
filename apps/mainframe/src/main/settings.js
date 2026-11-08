/* Settings from the Settings page, kept in settings.json in the app's data folder.

   Every setting is named after the environment variable the Python side already reads, and the
   whole set is passed as environment to main.py and curator_service when they start. A variable
   that's already set in the environment wins and shows as locked on the Settings page.

   Secrets are encrypted with safeStorage (DPAPI on Windows, so only your Windows login can read
   them) and never sent to the Settings page: it only learns whether each one is set.
*/
import { app, safeStorage } from 'electron'
import { existsSync, readFileSync, writeFileSync } from 'fs'
import { join } from 'path'

// Which backend does each job, and the cloud backends' keys and models. The brain package reads
// them (empty means its own default); Settings › Integrations edits them.
export const BRAIN_KEYS = [
  'BRAIN_ROLE_CLASSIFY',
  'BRAIN_ROLE_EXTRACT',
  'BRAIN_ROLE_BRIEF',
  'BRAIN_ROLE_CURATE',
  'BRAIN_ROLE_JUDGE',
  'BRAIN_ROLE_REFLECT',
  'BRAIN_ROLE_LABEL',
  'BRAIN_ROLE_SCREEN',
  'DEEPSEEK_API_KEY',
  'OPENROUTER_API_KEY',
  'BRAIN_DEEPSEEK_MODEL',
  'BRAIN_OPENROUTER_MODEL',
  'BRAIN_CODEX_MODEL',
  'BRAIN_CODEX_REASONING',
  'BRAIN_CLAUDE_MODEL',
  'BRAIN_LOCAL_ONLY',
  'BRAIN_DAILY_USD',
  // Not the brain's own setting: the engine reads it to send screen changes to BRAIN_ROLE_SCREEN
  'OHM_SCREEN_CLOUD'
]

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
  'OHM_DISABLED_MODULES',
  'OHM_ENABLED_MODULES',
  ...BRAIN_KEYS
]

export const SECRETS = ['DISCORD_TOKEN', 'MEMORY_API_KEY', 'DEEPSEEK_API_KEY', 'OPENROUTER_API_KEY']

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

const encrypt = (value) =>
  value && safeStorage.isEncryptionAvailable()
    ? 'enc:' + safeStorage.encryptString(value).toString('base64')
    : value

function decrypt(value) {
  if (!value?.startsWith('enc:')) return value
  try {
    return safeStorage.decryptString(Buffer.from(value.slice(4), 'base64'))
  } catch {
    console.error('Could not decrypt a saved secret; enter it again in Settings.')
    return ''
  }
}

/** One setting: the environment, then settings.json. */
export function setting(key) {
  return process.env[key] || decrypt(load()[key]) || ''
}

/** Everything the Settings page needs. Secrets come back empty, with `secretsSet` saying which exist. */
export function getSettings() {
  const values = {}
  for (const key of KEYS) values[key] = SECRETS.includes(key) ? '' : setting(key)
  return {
    values,
    secretsSet: SECRETS.filter((key) => setting(key)),
    locked: KEYS.filter((key) => process.env[key]),
    file: file()
  }
}

/** For a secret, '' keeps the saved one and null clears it. Also encrypts any secret still in plain text. */
export function saveSettings(values) {
  const next = { ...load() }
  for (const key of KEYS) {
    if (!(key in values) || process.env[key]) continue
    if (SECRETS.includes(key) && values[key] === '') continue
    next[key] = String(values[key] ?? '').trim()
  }
  for (const key of SECRETS) if (next[key] && !next[key].startsWith('enc:')) next[key] = encrypt(next[key])
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
