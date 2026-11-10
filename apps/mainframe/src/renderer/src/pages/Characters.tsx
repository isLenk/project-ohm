import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  LuCheck,
  LuChevronDown,
  LuExternalLink,
  LuFile,
  LuPlus,
  LuTrash2,
  LuUpload,
  LuX
} from 'react-icons/lu'
import { v4 as uuidv4 } from 'uuid'
import PageHeader from '../components/PageHeader'
import CharacterCard, { ICONS, hasImage } from '../components/CharacterCard'
import { dialog, followJob, get, post, put } from '../curator/api'
import { message, useCharacters, usePoll } from '../lib/ohm'
import { Loading, useBusy } from '../components/Loader'
import {
  ENGINES,
  KOKORO_DEFAULT,
  KOKORO_VOICES,
  engineOf,
  sameVoice,
  voiceName
} from '../lib/voices'

// Crops an uploaded image to a 256px square so it fits in the character record.
const toIcon = async (file: File) => {
  const img = await createImageBitmap(file)
  const side = Math.min(img.width, img.height)
  const canvas = Object.assign(document.createElement('canvas'), { width: 256, height: 256 })
  canvas
    .getContext('2d')
    .drawImage(img, (img.width - side) / 2, (img.height - side) / 2, side, side, 0, 0, 256, 256)
  return canvas.toDataURL('image/webp', 0.85)
}

const IMPORT = '__import'

// Deep compare for the editor: ignores status, null/''/missing are equal
const empty = (v) => v == null || v === ''
const same = (a, b) => {
  if (empty(a) && empty(b)) return true
  if (typeof a !== 'object' || typeof b !== 'object' || !a || !b) return a === b
  const keys = new Set([...Object.keys(a), ...Object.keys(b)])
  keys.delete('status')
  return [...keys].every((k) => same(a[k], b[k]))
}
const fileName = (path: string) => path.split(/[\\/]/).pop()

// Chatterbox won't clone from less than this (and only listens to the first ~10 seconds)
const MIN_CLIP_SECONDS = 5
const clipSeconds = async (bytes: ArrayBuffer) => {
  const ctx = new AudioContext()
  try {
    return (await ctx.decodeAudioData(bytes.slice(0))).duration
  } catch {
    throw new Error("That file couldn't be read as audio.")
  } finally {
    ctx.close()
  }
}

const Characters = () => {
  const navigate = useNavigate()
  const { list, setList } = useCharacters()
  const characters = list ?? []
  const { data: models, error: modelsError } = usePoll(() => get('/models'), 30000)
  const [draft, setDraft] = useState(null) // the character open in the editor
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  // Last save result, shown beside Save
  const [saveStatus, setSaveStatus] = useState<{ id: string; error?: string } | null>(null)
  const [voices, setVoices] = useState<string[]>(null) // clips in apps/tts/voices
  const voiceInput = useRef<HTMLInputElement>(null)

  useEffect(() => {
    window.api.listVoices().then(setVoices, () => setVoices([]))
  }, [])

  useEffect(() => {
    if (!draft && list?.length) setDraft(list.find((c) => c.status === 'Active') ?? list[0])
  }, [list])

  const busy = useBusy()
  const run = (label, fn) =>
    busy
      .run(label, fn)
      .then(() => setError(''))
      .catch((e) => setError(message(e)))
  const pick = (params) => dialog('showOpenDialog', { properties: ['openFile'], ...params })

  const bases = models?.base_models ?? []
  const baseOf = (c) => bases.find((b) => b.id === c?.model?.base_model_id)
  const adapterOf = (c) => models?.adapters.find((a) => a.id === c?.model?.adapter_id)
  // Names come from the curator; the ids are saved on the card. Without the curator the ids are
  // still there, so say so instead of looking like nothing was picked.
  const missing = (what) =>
    models ? `${what} not found` : modelsError ? `${what} saved, curator offline` : 'Loading…'
  const modelLabel = (c) => {
    if (!c?.model?.base_model_id) return 'No model picked'
    const base = baseOf(c)
    if (!base) return missing('Model')
    const adapter = adapterOf(c)
    return `${base.name}${adapter ? ` + ${adapter.name}` : c.model.adapter_id ? ` + ${missing('LoRA')}` : ''}`
  }

  const save = async (c) => {
    const saved = (await window.api.updateCharacter(c)) || c
    setList((l) => l.map((x) => (x.id === saved.id ? saved : x)))
    return saved
  }

  const add = () =>
    run('Drawing a new card…', async () => {
      const fresh = {
        id: uuidv4(),
        name: 'New Character',
        created: new Date().toISOString().split('T')[0],
        status: 'Inactive',
        description: '',
        icon: 'star',
        model: draft?.model ?? {},
        voice: {}
      }
      // The NAS assigns the real id (a slug), so use what comes back.
      const saved = (await window.api.addCharacter(fresh)) || fresh
      setList((l) => [...l, saved])
      setDraft(saved)
    })

  /* Puts c's model on llama-server: switches the adapter live, or restarts it for a new base.
     Returns what to tell the user about it. */
  const switchModel = async (c, relabel) => {
    if (!c.model?.base_model_id) return 'Pick a base model to switch the LLM too.'
    const r = await put('/models/active', {
      base_model_id: c.model.base_model_id,
      adapter_id: c.model.adapter_id ?? null
    })
    if (!r.llm?.running) return 'Their model loads with the engine.'
    if (r.applied) return ''
    relabel(`Restarting llama-server on ${modelLabel(c)}…`)
    const job = await post('/llm/restart')
    const result = await followJob(job.id, () => {})
    return result?.ok
      ? `Running on ${modelLabel(c)}.`
      : 'llama-server did not come back. Check its window.'
  }

  /* Preloads c's voice so their first reply doesn't wait on it. Returns what to tell the user. */
  const switchVoice = async (c, relabel) => {
    const engine = engineOf(c.voice)
    const file = voiceName(c.voice)
    relabel(
      engine === 'kokoro'
        ? `Loading Kokoro for ${c.name}…`
        : file
          ? `Loading ${c.name}'s voice…`
          : `Switching to the built-in voice…`
    )
    try {
      await window.api.loadVoice({ engine, file })
      return '' // or the TTS server isn't up: the engine loads it when it is
    } catch (e) {
      const fallback = engine === 'kokoro' ? "Kokoro's default voice" : 'the built-in voice'
      return `Couldn't load ${c.name}'s voice: ${message(e)} They'll use ${fallback}.`
    }
  }

  /* One card in play at a time: activate this one, deactivate whichever was, switch the LLM to
     its model and the TTS to its voice, and restart the engine, which loads the card in play when
     it starts. */
  const play = (c) =>
    run(`Putting ${c.name} in play…`, async (relabel) => {
      await Promise.all(
        characters
          .filter((x) => (x.id === c.id) !== (x.status === 'Active'))
          .map((x) => save({ ...x, status: x.id === c.id ? 'Active' : 'Inactive' }))
      )
      setDraft((d) => (d?.id === c.id ? { ...d, status: 'Active' } : d))
      const notes = [`${c.name} is in play.`, await switchModel(c, relabel)]
      // The saved voice, not the editor's: that's what the engine reads from the card
      notes.push(await switchVoice(characters.find((x) => x.id === c.id) ?? c, relabel))
      const engine = await window.api.engineStatus()
      if (engine.external) {
        // Started by hand: killing it would leave its script and ours both starting one
        notes.push(`Restart main.py to talk as ${c.name}.`)
      } else if (engine.state !== 'stopped') {
        relabel(`Restarting the engine as ${c.name}…`)
        await window.api.restartEngine()
      }
      setNote(notes.filter(Boolean).join(' '))
    })

  const remove = async () => {
    const { response } = await window.api.openDialog('showMessageBox', {
      type: 'warning',
      title: 'Delete Character',
      message: `Delete ${draft.name}?`,
      detail:
        'This also deletes all of their memories, facts and chat history. This cannot be undone.',
      buttons: ['Delete', 'Cancel'],
      defaultId: 1,
      cancelId: 1
    })
    if (response !== 0) return
    run(`Deleting ${draft.name}…`, async () => {
      await window.api.deleteCharacter(draft.id)
      setList((l) => l.filter((c) => c.id !== draft.id))
      setDraft(null)
    })
  }

  const edit = (changes) => setDraft({ ...draft, ...changes })
  const editModel = (changes) => edit({ model: { ...draft.model, ...changes } })

  // Status is owned by "Play this card", so take the current one rather than the draft's.
  // Saving the card in play with a different model puts it on llama-server too, like "Play this card".
  // The bot reads the card on every reply, but the Engine panel and llama-server's own scales would
  // otherwise keep showing the old LoRA.
  const saveDraft = () =>
    run('Saving…', async (relabel) => {
      const before = characters.find((c) => c.id === draft.id)
      let saved
      try {
        saved = await save({ ...draft, status: before?.status })
      } catch (e) {
        setNote('')
        return setSaveStatus({ id: draft.id, error: message(e) })
      }
      setDraft(saved)
      setSaveStatus({ id: saved.id })
      const ids = (c) => `${c?.model?.base_model_id ?? ''}|${c?.model?.adapter_id ?? ''}`
      const inPlay = before?.status === 'Active'
      const switched = inPlay && ids(before) !== ids(saved) ? await switchModel(saved, relabel) : ''
      // The running engine re-reads the card's voice every 30 seconds; this checks the clip works
      // and has it loaded by then
      const voiced =
        inPlay && !sameVoice(before?.voice, saved.voice)
          ? (await switchVoice(saved, relabel)) ||
            `${saved.name}'s new voice is used from their next reply (within 30 seconds).`
          : ''
      setNote([switched, voiced].filter(Boolean).join(' '))
    })

  const pickBase = async (value) => {
    if (value !== IMPORT) return editModel({ base_model_id: value || null, adapter_id: null })
    const r = await pick({ filters: [{ name: 'GGUF model', extensions: ['gguf'] }] })
    if (r.canceled) return
    run(`Adding ${fileName(r.filePaths[0])}…`, async () => {
      const after = await post('/models/base', { gguf_path: r.filePaths[0] })
      const added =
        after.base_models.find((b) => b.gguf_path.toLowerCase() === r.filePaths[0].toLowerCase()) ??
        after.base_models.at(-1)
      editModel({ base_model_id: added.id, adapter_id: null })
    })
  }

  const pickAdapter = async (value) => {
    if (value !== IMPORT) return editModel({ adapter_id: value || null })
    const r = await pick({ filters: [{ name: 'LoRA adapter (GGUF)', extensions: ['gguf'] }] })
    if (r.canceled) return
    run(`Adding ${fileName(r.filePaths[0])}…`, async () => {
      const after = await post('/models/adapters', {
        path: r.filePaths[0],
        base_model_id: draft.model.base_model_id
      })
      editModel({ adapter_id: after.adapters[0].id })
    })
  }

  const pickVoice = (value) => {
    if (value === IMPORT) return voiceInput.current?.click()
    const { file: _, ...rest } = draft.voice ?? {}
    edit({ voice: value ? { ...rest, file: value } : rest })
  }

  // Keeps the other engine's pick for switching back
  const pickEngine = (engine) =>
    edit({
      voice: {
        ...draft.voice,
        engine,
        ...(engine === 'kokoro' && !draft.voice?.preset ? { preset: KOKORO_DEFAULT } : {})
      }
    })
  const pickPreset = (preset) => edit({ voice: { ...draft.voice, preset } })

  const importVoice = (file: File) =>
    run(`Adding ${file.name}…`, async () => {
      const bytes = await file.arrayBuffer()
      const seconds = await clipSeconds(bytes)
      if (seconds <= MIN_CLIP_SECONDS)
        throw new Error(
          `${file.name} is only ${seconds.toFixed(1)} seconds. A voice clip needs more than ${MIN_CLIP_SECONDS} seconds of speech.`
        )
      const saved = await window.api.importVoice(file.name, bytes)
      setVoices(await window.api.listVoices())
      setDraft((d) => ({ ...d, voice: { ...d.voice, file: saved } }))
    })

  const attachProject = async () => {
    const r = await pick({ filters: [{ name: 'Ohm project', extensions: ['ohm'] }] })
    if (!r.canceled) editModel({ ohm_path: r.filePaths[0] })
  }

  const openProject = () =>
    run(`Opening ${fileName(draft.model.ohm_path)}…`, async () => {
      await post('/project/open', { path: draft.model.ohm_path })
      navigate('/curator')
    })

  const base = baseOf(draft)
  const adapters = (models?.adapters ?? []).filter(
    (a) => a.base_model_id === draft?.model?.base_model_id
  )
  const stored = characters.find((c) => c.id === draft?.id)
  const inPlay = stored?.status === 'Active'
  const dirty = !!draft && !same(draft, stored)
  const status = saveStatus?.id === draft?.id ? saveStatus : null

  return (
    <>
      <PageHeader title="Characters" sub="Your deck. One card is in play at a time.">
        <button onClick={add} className="btn btn-primary">
          <LuPlus /> New character
        </button>
      </PageHeader>

      {(error || note) && (
        <p
          className={`border-b border-line px-8 py-2 text-[12.5px] ${error ? 'text-bad' : 'text-soft'}`}
        >
          {error || note}
        </p>
      )}

      <div className="flex flex-wrap">
        <section
          aria-label="Deck"
          className="grid min-w-0 flex-[999_1_520px] grid-cols-[repeat(auto-fill,minmax(180px,1fr))] content-start gap-x-6 gap-y-7 px-8 pt-7 pb-10"
        >
          {!list && <Loading label="Loading characters…" className="col-span-full" />}
          {characters.map((c) => (
            <CharacterCard
              key={c.id}
              c={c}
              model={modelLabel(c)}
              editing={draft?.id === c.id}
              onClick={() => setDraft(c)}
            />
          ))}
          <button
            onClick={add}
            className="flex aspect-[5/7] cursor-pointer flex-col items-center justify-center gap-3 rounded-[10px] border border-dashed border-edge text-soft hover:border-[#4a4d55] hover:text-gray-100"
          >
            <span className="grid size-11 place-items-center rounded-full border border-[#4a4d55]">
              <LuPlus className="size-4.5" />
            </span>
            <span className="text-[13px] font-medium">Draw a new card</span>
          </button>
        </section>

        {draft && (
          <aside
            aria-label="Edit character"
            className="flex max-w-full flex-[1_1_380px] flex-col gap-5 border-l border-line px-7 pt-7 pb-10"
          >
            <div className="flex items-center gap-3">
              <h2 className="flex-1 truncate text-[15px] font-semibold">Editing {draft.name}</h2>
              {inPlay ? (
                <span className="text-[12.5px] text-clay">In play</span>
              ) : (
                <button className="btn" onClick={() => play(draft)}>
                  Play this card
                </button>
              )}
            </div>

            <div className="flex flex-col gap-1.5 text-[12.5px] text-soft">
              Icon
              <div className="flex flex-wrap gap-2">
                {Object.entries(ICONS).map(([key, Icon]) => {
                  const on = (draft.icon ?? 'star') === key && !hasImage(draft)
                  return (
                    <button
                      key={key}
                      aria-label={key}
                      aria-pressed={on}
                      onClick={() => edit({ icon: key, picture: '' })}
                      className={`grid size-11 cursor-pointer place-items-center rounded-md border ${on ? 'border-accent text-clay' : 'border-edge text-soft hover:bg-raised'}`}
                    >
                      <Icon className="size-4.5" />
                    </button>
                  )
                })}
                <label className="btn h-11 border-dashed text-soft">
                  <LuUpload /> Upload
                  <input
                    type="file"
                    accept="image/*"
                    hidden
                    onChange={(e) =>
                      e.target.files[0] &&
                      toIcon(e.target.files[0]).then((picture) => edit({ picture }))
                    }
                  />
                </label>
              </div>
            </div>

            <label className="flex flex-col gap-1.5 text-[12.5px] text-soft">
              Name
              <input
                className="field"
                value={draft.name}
                onChange={(e) => edit({ name: e.target.value })}
              />
            </label>
            <label className="flex flex-col gap-1.5 text-[12.5px] text-soft">
              Personality and backstory
              <textarea
                rows={9}
                className="field h-auto resize-y py-2.5 leading-relaxed"
                value={draft.description}
                onChange={(e) => edit({ description: e.target.value })}
              />
            </label>

            <div className="flex flex-col gap-3.5 border-t border-line pt-4.5">
              <div className="flex items-baseline gap-2.5">
                <h3 className="text-[13.5px] font-semibold">Voice</h3>
                <span className="text-xs text-muted">What they sound like in voice calls</span>
              </div>
              <label className="flex flex-col gap-1.5 text-[12.5px] text-soft">
                Voice engine
                <Select value={engineOf(draft.voice)} onChange={pickEngine}>
                  {ENGINES.map((e) => (
                    <option key={e.id} value={e.id}>
                      {e.label}
                    </option>
                  ))}
                </Select>
                <span className="text-xs text-muted">
                  {ENGINES.find((e) => e.id === engineOf(draft.voice)).about}
                </span>
              </label>
              {engineOf(draft.voice) === 'kokoro' ? (
                <label className="flex flex-col gap-1.5 text-[12.5px] text-soft">
                  Kokoro voice
                  <Select value={voiceName(draft.voice)} onChange={pickPreset}>
                    {KOKORO_VOICES.map((v) => (
                      <option key={v.id} value={v.id}>
                        {v.label}
                      </option>
                    ))}
                  </Select>
                </label>
              ) : (
                <label className="flex flex-col gap-1.5 text-[12.5px] text-soft">
                  Voice clip
                  <Select value={draft.voice?.file ?? ''} onChange={pickVoice} disabled={!voices}>
                    <option value="">Built-in voice</option>
                    {draft.voice?.file && voices && !voices.includes(draft.voice.file) && (
                      <option value={draft.voice.file}>
                        {draft.voice.file} (not in apps/tts/voices)
                      </option>
                    )}
                    {(voices ?? []).map((v) => (
                      <option key={v} value={v}>
                        {v}
                      </option>
                    ))}
                    <option value={IMPORT}>Import a voice clip…</option>
                  </Select>
                  <span className="text-xs text-muted">
                    5 to 10 seconds of one person talking clearly, with no music or background
                    noise.
                  </span>
                </label>
              )}
              <input
                ref={voiceInput}
                type="file"
                accept=".wav,.flac,.mp3,.ogg"
                hidden
                onChange={(e) => {
                  const file = e.target.files?.[0]
                  e.target.value = '' // so picking the same file again still fires
                  if (file) importVoice(file)
                }}
              />
            </div>

            <div className="flex flex-col gap-3.5 border-t border-line pt-4.5">
              <div className="flex items-baseline gap-2.5">
                <h3 className="text-[13.5px] font-semibold">Model</h3>
                <span className="text-xs text-muted">Base model, then an optional LoRA on top</span>
              </div>
              {modelsError && <p className="text-xs text-bad">Models: {modelsError}</p>}

              <label className="flex flex-col gap-1.5 text-[12.5px] text-soft">
                Base model
                <Select
                  value={draft.model?.base_model_id ?? ''}
                  onChange={pickBase}
                  disabled={!models}
                >
                  <option value="">None picked</option>
                  {draft.model?.base_model_id && !base && (
                    <option value={draft.model.base_model_id}>{missing('Model')}</option>
                  )}
                  {bases.map((b) => (
                    <option key={b.id} value={b.id}>
                      {b.name}
                      {b.quant ? ` · ${b.quant}` : ''}
                    </option>
                  ))}
                  <option value={IMPORT}>Import a .gguf model…</option>
                </Select>
                {base && (
                  <span className="truncate font-mono text-[11px] text-muted">
                    {fileName(base.gguf_path)}
                  </span>
                )}
              </label>

              <label className="flex flex-col gap-1.5 text-[12.5px] text-soft">
                <span>
                  LoRA adapter <span className="text-muted">(optional)</span>
                </span>
                <Select
                  value={draft.model?.adapter_id ?? ''}
                  onChange={pickAdapter}
                  disabled={!base}
                >
                  <option value="">None, base model only</option>
                  {draft.model?.adapter_id && !adapterOf(draft) && (
                    <option value={draft.model.adapter_id}>{missing('LoRA')}</option>
                  )}
                  {adapters.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name}
                    </option>
                  ))}
                  {base && <option value={IMPORT}>Import a LoRA adapter…</option>}
                </Select>
                <span className="text-xs text-muted">
                  Only adapters trained on the base above are listed.
                </span>
              </label>

              {draft.model?.adapter_id && (
                <label className="flex flex-col gap-1.5 text-[12.5px] text-soft">
                  <span className="flex justify-between">
                    <span>LoRA strength</span>
                    <span className="font-mono text-gray-100">
                      {(draft.model?.lora_scale ?? 1).toFixed(2)}
                    </span>
                  </span>
                  <input
                    type="range"
                    min={0}
                    max={1}
                    step={0.05}
                    value={draft.model?.lora_scale ?? 1}
                    onChange={(e) => editModel({ lora_scale: Number(e.target.value) })}
                    className="accent-gray-200"
                  />
                  <span className="text-xs text-muted">
                    Lower keeps more of the base model’s sense: it answers what was said more often,
                    with less of the group’s voice. Applies from the next reply, no restart.
                  </span>
                </label>
              )}

              <div className="grid grid-cols-2 gap-3">
                {(
                  [
                    [
                      'temperature',
                      'Temperature',
                      '0.8',
                      'How loose replies are. 1 is the engine’s default; 0.8 is steadier.'
                    ],
                    [
                      'repeat_penalty',
                      'Repetition penalty',
                      '1.1',
                      'Stops a reply looping (“u / u / u”). 1 is off.'
                    ]
                  ] as const
                ).map(([key, label, placeholder, help]) => (
                  <label key={key} className="flex flex-col gap-1.5 text-[12.5px] text-soft">
                    <span>{label}</span>
                    <input
                      type="number"
                      step={0.05}
                      min={0}
                      max={2}
                      placeholder={placeholder}
                      value={draft.sampling?.[key] ?? ''}
                      onChange={(e) =>
                        edit({
                          sampling: {
                            ...draft.sampling,
                            [key]: e.target.value === '' ? null : Number(e.target.value)
                          }
                        })
                      }
                      className="h-9 rounded-md border border-edge bg-transparent px-2.5 font-mono text-[13px] text-gray-100"
                    />
                    <span className="text-xs text-muted">
                      {help} Takes effect when the engine restarts.
                    </span>
                  </label>
                ))}
              </div>

              <div className="flex flex-col gap-1.5 text-[12.5px] text-soft">
                <span>
                  Curator save file <span className="text-muted">(optional)</span>
                </span>
                {draft.model?.ohm_path ? (
                  <span className="flex min-h-11 items-center gap-2.5 rounded-md border border-edge py-1.5 pr-1 pl-3 text-gray-100">
                    <LuFile className="size-4 shrink-0 text-soft" />
                    <span
                      className="min-w-0 flex-1 truncate font-mono text-[12.5px]"
                      title={draft.model.ohm_path}
                    >
                      {fileName(draft.model.ohm_path)}
                    </span>
                    <button
                      onClick={openProject}
                      aria-label="Open in the curator"
                      title="Open in the curator"
                      className="grid size-8 shrink-0 cursor-pointer place-items-center rounded text-soft hover:bg-raised hover:text-gray-100"
                    >
                      <LuExternalLink className="size-[15px]" />
                    </button>
                    <button
                      onClick={() => editModel({ ohm_path: null })}
                      aria-label="Detach save file"
                      title="Detach"
                      className="grid size-8 shrink-0 cursor-pointer place-items-center rounded text-soft hover:bg-raised hover:text-gray-100"
                    >
                      <LuX className="size-[15px]" />
                    </button>
                  </span>
                ) : (
                  <button
                    onClick={attachProject}
                    className="btn h-11 justify-start border-dashed text-soft"
                  >
                    <LuPlus /> Attach a .ohm file…
                  </button>
                )}
                <span className="text-xs text-muted">
                  The curator project this character's LoRA trains from.
                </span>
              </div>
            </div>

            <div className="mt-1 flex items-center gap-2">
              <button onClick={remove} className="btn border-[#4a2a2a] text-bad">
                <LuTrash2 /> Delete
              </button>
              <span className="flex-1" />
              <span role="status" className="min-w-0 truncate text-[12.5px]">
                {status?.error ? (
                  <span className="text-bad" title={status.error}>
                    Not saved: {status.error}
                  </span>
                ) : dirty ? (
                  <span className="text-warn">Unsaved changes</span>
                ) : status ? (
                  <span className="flex items-center gap-1.5 text-good">
                    <LuCheck /> Saved
                  </span>
                ) : null}
              </span>
              <button onClick={saveDraft} disabled={!dirty} className="btn btn-primary">
                {dirty ? 'Save changes' : 'Saved'}
              </button>
            </div>
          </aside>
        )}
      </div>
      {busy.overlay}
    </>
  )
}

const Select = ({ value, onChange, disabled = false, children }) => (
  <span className="relative block">
    <select
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
      className="field cursor-pointer appearance-none pr-8 disabled:cursor-not-allowed disabled:opacity-50 [&>option]:bg-panel"
    >
      {children}
    </select>
    <LuChevronDown className="pointer-events-none absolute top-2.5 right-2.5 size-4 text-muted" />
  </span>
)

export default Characters
