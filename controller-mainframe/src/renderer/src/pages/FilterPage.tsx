import { useEffect, useState } from 'react'
import { LuX } from 'react-icons/lu'
import PageHeader from '../components/PageHeader'

type Lane = 'bleep' | 'block'
const LANES: Record<Lane, { title: string; chip: string }> = {
  bleep: { title: 'Bleep', chip: 'border-[#4a3d24] bg-[#1e1b14] text-[#f2d49a]' },
  block: { title: 'Block', chip: 'border-[#4a2a2a] bg-[#1e1515] text-[#f4b4b4]' }
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
        sub="Bleeped words are masked. Blocked words stop the reply. Click a word to move it."
      />

      <div className="flex flex-col gap-7 px-8 pt-6 pb-12">
        <form onSubmit={add} className="flex max-w-[760px] flex-wrap items-end gap-2.5">
          <label className="flex flex-[1_1_260px] flex-col gap-1.5 text-[12.5px] text-soft">
            Add a word
            <input
              className="field font-mono"
              placeholder="word or phrase"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
            />
          </label>
          <div
            role="radiogroup"
            aria-label="Lane"
            className="flex gap-0.5 rounded-md border border-edge p-[3px]"
          >
            {(['bleep', 'block'] as Lane[]).map((l) => (
              <button
                key={l}
                type="button"
                role="radio"
                aria-checked={lane === l}
                onClick={() => setLane(l)}
                className={`h-7 cursor-pointer rounded px-3 text-[12.5px] font-medium ${lane === l ? 'bg-[#26272b] text-gray-100' : 'text-muted'}`}
              >
                {LANES[l].title}
              </button>
            ))}
          </div>
          <button className="btn btn-primary">Add</button>
        </form>

        <div className="flex flex-wrap gap-x-12 gap-y-7">
          {(['bleep', 'block'] as Lane[]).map((l) => {
            const hidden = l === 'block' && !reveal
            return (
              <section
                key={l}
                aria-label={LANES[l].title}
                className="flex min-w-0 flex-[1_1_360px] flex-col"
              >
                <div className="flex min-h-12 items-center gap-3 border-b border-line">
                  <h2 className="text-[15px] font-semibold">{LANES[l].title}</h2>
                  <span className="font-mono text-[11.5px] text-muted">
                    {words[l].length} words
                  </span>
                  <span className="flex-1" />
                  {l === 'block' && (
                    <button
                      onClick={() => setReveal(!reveal)}
                      aria-pressed={reveal}
                      className="btn h-8"
                    >
                      {reveal ? 'Hide' : 'Reveal'}
                    </button>
                  )}
                </div>
                <div
                  className={`flex flex-wrap gap-2 pt-4 ${hidden ? 'pointer-events-none blur-[6px]' : ''}`}
                >
                  {words[l].map((w) => (
                    <span
                      key={w}
                      className={`inline-flex items-center rounded-md border ${LANES[l].chip}`}
                    >
                      <button
                        onClick={() => put(w, other(l))}
                        title={`Move to ${LANES[other(l)].title}`}
                        className="h-8 cursor-pointer pr-1 pl-3 font-mono text-[13px]"
                      >
                        {w}
                      </button>
                      <button
                        onClick={() => setWords(remove(w))}
                        aria-label={`Remove ${w}`}
                        className="grid size-8 cursor-pointer place-items-center opacity-60 hover:opacity-100"
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
        <p className="text-[12.5px] text-muted">
          Not wired to the bot yet. Lists are kept in this window only.
        </p>
      </div>
    </>
  )
}

export default FilterPage
