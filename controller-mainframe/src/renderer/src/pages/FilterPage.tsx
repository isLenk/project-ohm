import { useEffect, useState } from 'react'
import { LuX } from 'react-icons/lu'
import PageHeader from '../components/PageHeader'

type Lane = 'bleep' | 'block'
const LANES: Record<Lane, { title: string; chip: string }> = {
  bleep: { title: 'Bleep', chip: 'border-amber-900 bg-[#2e2a22] text-amber-200' },
  block: { title: 'Block', chip: 'border-red-900 bg-[#2e2222] text-red-200' }
}
const other = (lane: Lane): Lane => (lane === 'bleep' ? 'block' : 'bleep')

// Not wired to the bot yet, so the lists live in this window's localStorage.
const saved = () => {
  try {
    return JSON.parse(localStorage.getItem('filters'))
  } catch {
    return null
  }
}

const FilterPage = () => {
  const [words, setWords] = useState(
    () => saved() ?? { bleep: ['dumb', 'brainless', 'stupid'], block: ['idiot', 'suck'] }
  )
  const [draft, setDraft] = useState('')
  const [lane, setLane] = useState<Lane>('bleep')
  const [reveal, setReveal] = useState(false)

  useEffect(() => localStorage.setItem('filters', JSON.stringify(words)), [words])

  const remove = (w: string) => ({
    bleep: words.bleep.filter((x) => x !== w),
    block: words.block.filter((x) => x !== w)
  })
  const put = (w: string, to: Lane) => {
    const rest = remove(w)
    setWords({ ...rest, [to]: [...rest[to], w] })
  }
  const add = (e) => {
    e.preventDefault()
    if (draft.trim()) put(draft.trim().toLowerCase(), lane)
    setDraft('')
  }

  return (
    <>
      <PageHeader
        title="Filters"
        sub="Click a word to move it to the other lane. Bleeped words are masked; blocked words stop the reply."
      />

      <form onSubmit={add} className="panel mb-5 flex flex-wrap items-end gap-2.5 p-4">
        <label className="flex flex-1 basis-60 flex-col gap-1.5 text-xs text-muted">
          Add a word
          <input className="field" value={draft} onChange={(e) => setDraft(e.target.value)} />
        </label>
        <div className="flex overflow-hidden rounded-lg border border-line">
          {(['bleep', 'block'] as Lane[]).map((l) => (
            <button
              key={l}
              type="button"
              aria-pressed={lane === l}
              onClick={() => setLane(l)}
              className={`btn min-h-11 rounded-none ${lane === l ? 'bg-gray-100 text-ink' : 'bg-field text-muted'}`}
            >
              {LANES[l].title}
            </button>
          ))}
        </div>
        <button className="btn min-h-11 bg-accent text-ink">Add</button>
      </form>

      <div className="grid gap-5 md:grid-cols-2">
        {(['bleep', 'block'] as Lane[]).map((l) => {
          const hidden = l === 'block' && !reveal
          return (
            <section key={l} className="panel flex min-h-72 flex-col gap-4 p-5">
              <div className="flex items-center justify-between">
                <h2 className="font-display text-xl">{LANES[l].title}</h2>
                {l === 'block' ? (
                  <button onClick={() => setReveal(!reveal)} className="btn min-h-9 rounded-full border border-line">
                    {reveal ? 'Hide' : 'Reveal'}
                  </button>
                ) : (
                  <span className="font-mono text-xs text-muted">{words[l].length} words</span>
                )}
              </div>
              <div className={`flex flex-wrap gap-2 ${hidden ? 'pointer-events-none blur-md' : ''}`}>
                {words[l].map((w) => (
                  <span key={w} className={`inline-flex items-center rounded-full border ${LANES[l].chip}`}>
                    <button
                      onClick={() => put(w, other(l))}
                      title={`Move to ${LANES[other(l)].title}`}
                      className="min-h-10 cursor-pointer pr-1.5 pl-4 font-mono text-sm"
                    >
                      {w}
                    </button>
                    <button
                      onClick={() => setWords(remove(w))}
                      aria-label={`Remove ${w}`}
                      className="grid size-10 cursor-pointer place-items-center text-muted"
                    >
                      <LuX />
                    </button>
                  </span>
                ))}
              </div>
            </section>
          )
        })}
      </div>
    </>
  )
}

export default FilterPage
