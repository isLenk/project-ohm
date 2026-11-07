import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
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
const fileName = (path: string) => path.split(/[\\/]/).pop()

const Characters = () => {
  const navigate = useNavigate()
  const { list, setList } = useCharacters()
  const characters = list ?? []
  const { data: models, error: modelsError } = usePoll(() => get('/models'), 30000)
  const [draft, setDraft] = useState(null) // the character open in the editor
  const [error, setError] = useState('')
  const [note, setNote] = useState('')

  useEffect(() => {
    if (!draft && list?.length) setDraft(list.find((c) => c.status === 'Active') ?? list[0])
  }, [list])

  const run = (fn) =>
    fn()
      .then(() => setError(''))
      .catch((e) => setError(message(e)))

  const bases = models?.base_models ?? []
  const baseOf = (c) => bases.find((b) => b.id === c?.model?.base_model_id)
  const adapterOf = (c) => models?.adapters.find((a) => a.id === c?.model?.adapter_id)
  const modelLabel = (c) => {
    const base = baseOf(c)
    const adapter = adapterOf(c)
    return base ? `${base.name}${adapter ? ` + ${adapter.name}` : ''}` : 'No model picked'
  }

  const save = async (c) => {
    const saved = (await window.api.updateCharacter(c)) || c
    setList((l) => l.map((x) => (x.id === saved.id ? saved : x)))
    return saved
  }

  const add = () =>
    run(async () => {
      const fresh = {
        id: uuidv4(),
        name: 'New Character',
        created: new Date().toISOString().split('T')[0],
        status: 'Inactive',
        description: '',
        icon: 'star',
        model: draft?.model ?? {}
      }
      // The NAS assigns the real id (a slug), so use what comes back.
      const saved = (await window.api.addCharacter(fresh)) || fresh
      setList((l) => [...l, saved])
      setDraft(saved)
    })

  /* One card in play at a time: activate this one, deactivate whichever was, and put its
     model on llama-server (switching the adapter live, or restarting it for a new base). */
  const play = (c) =>
    run(async () => {
      await Promise.all(
        characters
          .filter((x) => (x.id === c.id) !== (x.status === 'Active'))
          .map((x) => save({ ...x, status: x.id === c.id ? 'Active' : 'Inactive' }))
      )
      setDraft((d) => (d?.id === c.id ? { ...d, status: 'Active' } : d))
      if (!c.model?.base_model_id)
        return setNote(`${c.name} is in play. Pick a base model to switch the LLM too.`)
      const r = await put('/models/active', {
        base_model_id: c.model.base_model_id,
        adapter_id: c.model.adapter_id ?? null
      })
      if (r.applied || !r.llm?.running) {
        setNote(
          r.llm?.running
            ? `${c.name} is in play.`
            : `${c.name} is in play. Their model loads with the engine.`
        )
        return
      }
      setNote(`Restarting llama-server on ${modelLabel(c)}…`)
      const job = await post('/llm/restart')
      const result = await followJob(job.id, () => {})
      setNote(
        result?.ok
          ? `${c.name} is in play on ${modelLabel(c)}.`
          : 'llama-server did not come back. Check its window.'
      )
    })

  const remove = () =>
    run(async () => {
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
      await window.api.deleteCharacter(draft.id)
      setList((l) => l.filter((c) => c.id !== draft.id))
      setDraft(null)
    })

  const edit = (changes) => setDraft({ ...draft, ...changes })
  const editModel = (changes) => edit({ model: { ...draft.model, ...changes } })

  // Status is owned by "Play this card", so take the current one rather than the draft's.
  const saveDraft = () =>
    run(async () => {
      const saved = await save({
        ...draft,
        status: characters.find((c) => c.id === draft.id)?.status
      })
      setDraft(saved)
      setNote('Saved.')
    })

  const pickBase = (value) =>
    run(async () => {
      if (value !== IMPORT) return editModel({ base_model_id: value || null, adapter_id: null })
      const r = await dialog('showOpenDialog', {
        properties: ['openFile'],
        filters: [{ name: 'GGUF model', extensions: ['gguf'] }]
      })
      if (r.canceled) return
      const after = await post('/models/base', { gguf_path: r.filePaths[0] })
      const added =
        after.base_models.find((b) => b.gguf_path.toLowerCase() === r.filePaths[0].toLowerCase()) ??
        after.base_models.at(-1)
      editModel({ base_model_id: added.id, adapter_id: null })
    })

  const pickAdapter = (value) =>
    run(async () => {
      if (value !== IMPORT) return editModel({ adapter_id: value || null })
      const r = await dialog('showOpenDialog', {
        properties: ['openFile'],
        filters: [{ name: 'LoRA adapter (GGUF)', extensions: ['gguf'] }]
      })
      if (r.canceled) return
      const after = await post('/models/adapters', {
        path: r.filePaths[0],
        base_model_id: draft.model.base_model_id
      })
      editModel({ adapter_id: after.adapters[0].id })
    })

  const attachProject = () =>
    run(async () => {
      const r = await dialog('showOpenDialog', {
        properties: ['openFile'],
        filters: [{ name: 'Ohm project', extensions: ['ohm'] }]
      })
      if (!r.canceled) editModel({ ohm_path: r.filePaths[0] })
    })

  const openProject = () =>
    run(async () => {
      await post('/project/open', { path: draft.model.ohm_path })
      navigate('/curator')
    })

  const base = baseOf(draft)
  const adapters = (models?.adapters ?? []).filter(
    (a) => a.base_model_id === draft?.model?.base_model_id
  )
  const inPlay = characters.find((c) => c.id === draft?.id)?.status === 'Active'

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
                <h3 className="text-[13.5px] font-semibold">Model</h3>
                <span className="text-xs text-muted">Base model, then an optional LoRA on top</span>
              </div>
              {modelsError && <p className="text-xs text-bad">Models: {modelsError}</p>}

              <label className="flex flex-col gap-1.5 text-[12.5px] text-soft">
                Base model
                <Select value={draft.model?.base_model_id ?? ''} onChange={pickBase}>
                  <option value="">None picked</option>
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
              <button onClick={saveDraft} className="btn btn-primary">
                Save changes
              </button>
            </div>
          </aside>
        )}
      </div>
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
