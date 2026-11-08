/* Character storage for the controller UI.

   When the memory service address is set (Settings › Memory), the NAS is the only copy:
   the UI reads and writes characters there, and the bot loads them from there too.
   Without it, the old local nedb file (characters.db) is used.

   The UI works with this shape:
     { id, name, created, status, description, picture, icon, model, voice }
   `picture` is a data: URL when the user uploaded one; otherwise the card shows `icon`.
   `model` is { base_model_id, adapter_id, ohm_path }: the base and LoRA from the curator's
   model registry, and the curator project the LoRA is trained from. All optional.
   `voice` is { file }: a reference clip in apps/tts/voices to speak as (see voices.js);
   {} is the TTS's built-in voice.
   On the NAS, `id` is the character slug ('ohm'), `description` is the personality,
   `voice` is its own column (the engine reads it from there), and
   created/status/picture/icon/model live in configs.ui.
*/
import { charactersDB } from './db.js'
import { nas, nasError } from './nas.js'

function toUi(row) {
  const ui = row.configs?.ui || {}
  return {
    id: row.slug,
    name: row.name,
    created: ui.created || String(row.created_at).slice(0, 10),
    status: ui.status || 'Inactive',
    description: row.personality || '',
    picture: ui.picture || 'default.png',
    icon: ui.icon || 'star',
    model: ui.model || {},
    voice: row.voice || {}
  }
}

function uiConfigs(c) {
  return {
    ui: {
      created: c.created,
      status: c.status,
      picture: c.picture,
      icon: c.icon,
      model: c.model || {}
    }
  }
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
  const api = nas()
  if (!api) return local.list()
  try {
    const { data } = await api.get('/characters')
    return data.map(toUi)
  } catch (e) {
    throw nasError(e)
  }
}

/* Creates a character. The returned object has the real id (slug), which the UI
   must use from then on. */
export async function addCharacter(c) {
  const api = nas()
  if (!api) return local.add(c)
  try {
    const { data: existing } = await api.get('/characters')
    const taken = new Set(existing.map((r) => r.slug))
    const base = slugify(c.name)
    let slug = base
    for (let i = 2; taken.has(slug); i++) slug = `${base}-${i}`

    const { data } = await api.put(`/characters/${slug}`, {
      name: c.name,
      personality: c.description,
      configs: uiConfigs(c),
      voice: c.voice || {}
    })
    return toUi(data)
  } catch (e) {
    throw nasError(e)
  }
}

export async function updateCharacter(c) {
  const api = nas()
  if (!api) return local.update(c)
  try {
    // PATCH merges configs, so model/max_tokens set elsewhere are kept. voice is replaced whole.
    const { data } = await api.patch(`/characters/${encodeURIComponent(c.id)}`, {
      name: c.name,
      personality: c.description,
      configs: uiConfigs(c),
      voice: c.voice || {}
    })
    return toUi(data)
  } catch (e) {
    throw nasError(e)
  }
}

export async function deleteCharacter(id) {
  const api = nas()
  if (!api) return local.remove(id)
  try {
    await api.delete(`/characters/${encodeURIComponent(id)}`)
    return 1
  } catch (e) {
    throw nasError(e)
  }
}
