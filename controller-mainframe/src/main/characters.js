/* Character storage for the controller UI.

   When MAIN_VITE_MEMORY_API_URL is set, the NAS memory service is the only copy:
   the UI reads and writes characters there, and the bot loads them from there too.
   Without it, the old local nedb file (characters.db) is used.

   The UI works with this shape:
     { id, name, created, status, description, picture, icon }
   `picture` is a data: URL when the user uploaded one; otherwise the card shows `icon`.
   On the NAS, `id` is the character slug ('ohm'), `description` is the personality,
   and created/status/picture live in configs.ui.
*/
import axios from 'axios'
import { charactersDB } from './db.js'

const NAS_URL = import.meta.env.MAIN_VITE_MEMORY_API_URL
const nas = NAS_URL
  ? axios.create({
      baseURL: NAS_URL,
      headers: { 'X-API-Key': import.meta.env.MAIN_VITE_MEMORY_API_KEY || '' },
      timeout: 5000
    })
  : null

export const usingNas = Boolean(nas)

function nasError(error) {
  const detail = error.response?.data?.detail || error.message
  return new Error(`Memory service (${NAS_URL}): ${detail}`)
}

function toUi(row) {
  const ui = row.configs?.ui || {}
  return {
    id: row.slug,
    name: row.name,
    created: ui.created || String(row.created_at).slice(0, 10),
    status: ui.status || 'Inactive',
    description: row.personality || '',
    picture: ui.picture || 'default.png',
    icon: ui.icon || 'star'
  }
}

function uiConfigs(c) {
  return { ui: { created: c.created, status: c.status, picture: c.picture, icon: c.icon } }
}

function slugify(name) {
  return (
    String(name || '')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '') || 'character'
  )
}

// ── nedb fallback ────────────────────────────────────────────────────────────
const local = {
  list: () =>
    new Promise((resolve, reject) =>
      charactersDB.find({}, (err, docs) => (err ? reject(err) : resolve(docs)))
    ),
  add: (c) =>
    new Promise((resolve, reject) =>
      charactersDB.insert(c, (err, doc) => (err ? reject(err) : resolve(doc)))
    ),
  update: (c) =>
    new Promise((resolve, reject) =>
      charactersDB.update({ id: c.id }, { $set: c }, {}, (err) => (err ? reject(err) : resolve(c)))
    ),
  remove: (id) =>
    new Promise((resolve, reject) =>
      charactersDB.remove({ id }, {}, (err, n) => (err ? reject(err) : resolve(n)))
    )
}

// ── Public API (used by the IPC handlers) ────────────────────────────────────
export async function listCharacters() {
  if (!nas) return local.list()
  try {
    const { data } = await nas.get('/characters')
    return data.map(toUi)
  } catch (e) {
    throw nasError(e)
  }
}

/* Creates a character. The returned object has the real id (slug), which the UI
   must use from then on. */
export async function addCharacter(c) {
  if (!nas) return local.add(c)
  try {
    const { data: existing } = await nas.get('/characters')
    const taken = new Set(existing.map((r) => r.slug))
    const base = slugify(c.name)
    let slug = base
    for (let i = 2; taken.has(slug); i++) slug = `${base}-${i}`

    const { data } = await nas.put(`/characters/${slug}`, {
      name: c.name,
      personality: c.description,
      configs: uiConfigs(c)
    })
    return toUi(data)
  } catch (e) {
    throw nasError(e)
  }
}

export async function updateCharacter(c) {
  if (!nas) return local.update(c)
  try {
    // PATCH merges configs, so model/max_tokens/voice set elsewhere are kept.
    const { data } = await nas.patch(`/characters/${encodeURIComponent(c.id)}`, {
      name: c.name,
      personality: c.description,
      configs: uiConfigs(c)
    })
    return toUi(data)
  } catch (e) {
    throw nasError(e)
  }
}

export async function deleteCharacter(id) {
  if (!nas) return local.remove(id)
  try {
    await nas.delete(`/characters/${encodeURIComponent(id)}`)
    return 1
  } catch (e) {
    throw nasError(e)
  }
}
