import { useEffect, useState } from 'react'
import { LuPlus, LuTrash2, LuUpload } from 'react-icons/lu'
import { v4 as uuidv4 } from 'uuid'
import PageHeader from '../components/PageHeader'
import CharacterCard, { ICONS, hasImage } from '../components/CharacterCard'

// Crops an uploaded image to a 256px square so it fits in the character record.
const toIcon = async (file: File) => {
  const img = await createImageBitmap(file)
  const side = Math.min(img.width, img.height)
  const canvas = Object.assign(document.createElement('canvas'), { width: 256, height: 256 })
  canvas.getContext('2d').drawImage(img, (img.width - side) / 2, (img.height - side) / 2, side, side, 0, 0, 256, 256)
  return canvas.toDataURL('image/webp', 0.85)
}

const Characters = () => {
  const [characters, setCharacters] = useState([])
  const [draft, setDraft] = useState(null) // the character open in the editor
  const [error, setError] = useState('')

  // IPC errors arrive as "Error invoking remote method '...': Error: <message>"
  const run = (fn) =>
    fn()
      .then(() => setError(''))
      .catch((e) => setError(String(e?.message || e).replace(/^.*?Error: /, '')))

  useEffect(() => {
    run(async () => {
      const list = await window.api.getCharacters()
      setCharacters(list)
      setDraft(list[0] ?? null)
    })
  }, [])

  const save = async (c) => {
    const saved = (await window.api.updateCharacter(c)) || c
    setCharacters((list) => list.map((x) => (x.id === saved.id ? saved : x)))
  }

  const add = () =>
    run(async () => {
      const fresh = {
        id: uuidv4(),
        name: 'New Character',
        created: new Date().toISOString().split('T')[0],
        status: 'Inactive',
        description: '',
        icon: 'star'
      }
      // The NAS assigns the real id (a slug), so use what comes back.
      const saved = (await window.api.addCharacter(fresh)) || fresh
      setCharacters((list) => [...list, saved])
      setDraft(saved)
    })

  // One card in play at a time: activate this one, deactivate whichever was.
  const play = (id) =>
    run(() =>
      Promise.all(
        characters
          .filter((c) => (c.id === id) !== (c.status === 'Active'))
          .map((c) => save({ ...c, status: c.id === id ? 'Active' : 'Inactive' }))
      )
    )

  const remove = () =>
    run(async () => {
      const { response } = await window.api.openDialog('showMessageBox', {
        type: 'warning',
        title: 'Delete Character',
        message: `Delete ${draft.name}?`,
        detail: 'This also deletes all of their memories, facts and chat history. This cannot be undone.',
        buttons: ['Delete', 'Cancel'],
        defaultId: 1,
        cancelId: 1
      })
      if (response !== 0) return
      await window.api.deleteCharacter(draft.id)
      setCharacters((list) => list.filter((c) => c.id !== draft.id))
      setDraft(null)
    })

  const edit = (changes) => setDraft({ ...draft, ...changes })
  // Status is owned by "Play this card", so take the current one rather than the draft's.
  const saveDraft = () =>
    run(() => save({ ...draft, status: characters.find((c) => c.id === draft.id)?.status }))

  return (
    <>
      <PageHeader title="Characters" sub="Your deck. One card is in play at a time.">
        <button onClick={add} className="btn bg-accent text-ink">
          <LuPlus /> New character
        </button>
      </PageHeader>

      {error && <p className="mb-5 rounded-lg bg-red-500/20 px-3 py-2 text-sm text-red-200">{error}</p>}

      <div className="grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] items-start gap-x-7 gap-y-8">
        {characters.map((c) => (
          <CharacterCard
            key={c.id}
            c={c}
            editing={draft?.id === c.id}
            onPlay={() => play(c.id)}
            onEdit={() => setDraft(c)}
          />
        ))}
        <button
          onClick={add}
          className="grid aspect-[5/7] cursor-pointer rounded-[14px] bg-panel p-2.5 shadow-[0_12px_28px_rgba(0,0,0,.35)] [background-image:repeating-linear-gradient(45deg,#fbbf2412_0_2px,transparent_2px_14px),repeating-linear-gradient(-45deg,#fbbf2412_0_2px,transparent_2px_14px)]"
        >
          <span className="flex flex-col items-center justify-center gap-3 rounded-[9px] border-[1.5px] border-[#4a4a52] text-sm font-semibold">
            <span className="grid size-14 place-items-center rounded-full bg-accent text-ink">
              <LuPlus className="size-6" />
            </span>
            Draw a new card
          </span>
        </button>
      </div>

      {draft && (
        <section className="panel mt-10 flex flex-col gap-5 p-6">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold">Editing {draft.name}</h2>
            <button onClick={remove} className="btn border border-red-900 text-red-400">
              <LuTrash2 /> Delete
            </button>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {Object.entries(ICONS).map(([key, Icon]) => {
              const on = (draft.icon ?? 'star') === key && !hasImage(draft)
              return (
                <button
                  key={key}
                  aria-label={key}
                  aria-pressed={on}
                  onClick={() => edit({ icon: key, picture: '' })}
                  className={`grid size-12 cursor-pointer place-items-center rounded-lg border ${
                    on ? 'border-accent bg-[#3a3528] text-accent' : 'border-line bg-field'
                  }`}
                >
                  <Icon className="size-5" />
                </button>
              )
            })}
            <label className="btn min-h-12 border border-dashed border-line">
              <LuUpload /> Upload icon
              <input
                type="file"
                accept="image/*"
                hidden
                onChange={(e) => e.target.files[0] && toIcon(e.target.files[0]).then((picture) => edit({ picture }))}
              />
            </label>
          </div>

          <label className="flex flex-col gap-1.5 text-xs text-muted">
            Name
            <input className="field" value={draft.name} onChange={(e) => edit({ name: e.target.value })} />
          </label>
          <label className="flex flex-col gap-1.5 text-xs text-muted">
            Personality and backstory
            <textarea
              rows={4}
              className="field py-3 leading-relaxed"
              value={draft.description}
              onChange={(e) => edit({ description: e.target.value })}
            />
          </label>
          <button onClick={saveDraft} className="btn self-end bg-gray-100 text-ink">
            Save changes
          </button>
        </section>
      )}
    </>
  )
}

export default Characters
