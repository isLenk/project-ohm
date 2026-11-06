import { useCallback, useEffect, useState } from 'react'
import { LuCheck, LuLink, LuPencil, LuTrash2, LuUndo2 } from 'react-icons/lu'
import { followJob, get, patch, post } from './api'
import { color, cx, fmtTime, pct } from './util'

type Person = {
  author_id: string
  name: string
  display_name: string | null
  color_index: number
  is_bot: boolean
  messages: number
  conversations: number
  share: number
  memories: { new: number; kept: number; forgotten: number }
}

type Memory = {
  id: number
  author_id: string
  category: 'fact' | 'relationship' | 'style'
  text: string
  status: 'new' | 'kept' | 'forgotten'
  short_lived: boolean
  mentioned_name: string | null
  linked_author_id: string | null
  synced: boolean
  sources: { id: string; ts: number; content_text: string }[]
}

const GROUPS: [Memory['category'], string][] = [
  ['fact', 'Facts and habits'],
  ['relationship', 'People they know'],
  ['style', 'How they write']
]

type Props = {
  selected: string | null
  onSelect: (id: string) => void
  onDirty: () => void
  onError: (e: unknown) => void
  openMessage: (id: string) => void
}

export default function MemoriesView({ selected, onSelect, onDirty, onError, openMessage }: Props) {
  const [people, setPeople] = useState<Person[]>([])
  const [filter, setFilter] = useState('')
  const [tab, setTab] = useState<Memory['status']>('new')
  const [mems, setMems] = useState<Memory[]>([])
  const [editing, setEditing] = useState<number | null>(null)
  const [draft, setDraft] = useState('')
  const [adding, setAdding] = useState('')
  const [linking, setLinking] = useState<number | null>(null)
  const [job, setJob] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  const loadPeople = useCallback(async () => {
    try {
      const r = await get<{ people: Person[] }>('/people')
      setPeople(r.people)
      if (!selected && r.people.length) onSelect(r.people.find((p) => !p.is_bot)?.author_id || r.people[0].author_id)
    } catch (e) {
      onError(e)
    }
  }, [selected, onSelect, onError])

  const loadMems = useCallback(async () => {
    if (!selected) return
    try {
      const r = await get<{ memories: Memory[] }>(`/people/${selected}/memories?status=${tab}`)
      setMems(r.memories)
    } catch (e) {
      onError(e)
    }
  }, [selected, tab, onError])

  useEffect(() => {
    loadPeople()
  }, [loadPeople])
  useEffect(() => {
    loadMems()
  }, [loadMems])

  const refresh = () => {
    onDirty()
    loadMems()
    loadPeople()
  }

  const update = async (m: Memory, body: any) => {
    try {
      await patch(`/memories/${m.id}`, body)
      refresh()
    } catch (e) {
      onError(e)
    }
  }

  const extract = async () => {
    try {
      const j = await post('/memories/extract', {})
      setJob('Reading messages…')
      const result = await followJob(j.id, (p) => setJob(p.total ? `Reading messages ${p.done}/${p.total}` : 'Reading messages…'))
      setJob(null)
      setNote(`Found ${result?.created ?? 0} new memories (${result?.merged ?? 0} matched ones you already had).`)
      refresh()
    } catch (e) {
      setJob(null)
      onError(e)
    }
  }

  const sync = async () => {
    try {
      const r = await post('/memories/sync')
      setNote(r.failed ? `Synced ${r.synced}. ${r.failed} failed; is the NAS reachable?` : `Synced ${r.synced} memories to the memory service.`)
      onDirty()
    } catch (e) {
      onError(e)
    }
  }

  const person = people.find((p) => p.author_id === selected)
  const shown = people.filter((p) => (p.display_name || p.name).toLowerCase().includes(filter.toLowerCase()))

  return (
    <div className="flex min-h-0 flex-1 flex-wrap">
      <nav aria-label="People" className="flex w-[300px] max-w-full flex-col border-r border-[#2f3037]">
        <div className="flex flex-col gap-3 p-5">
          <p className="text-[12.5px] leading-relaxed text-muted">
            What Ohm picked up about each person. Kept memories go to the memory service, not into the LoRA.
          </p>
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter people"
            aria-label="Filter people"
            className="h-9 rounded-md border border-line bg-transparent px-2.5 text-sm outline-none focus:border-gray-400"
          />
          <div className="flex gap-2">
            <button onClick={extract} disabled={!!job} className="flex-1 rounded-md border border-line px-2 py-1.5 text-xs hover:bg-raised disabled:opacity-60">
              {job || 'Read messages for memories'}
            </button>
            <button onClick={sync} className="rounded-md border border-line px-2 py-1.5 text-xs hover:bg-raised">
              Sync kept
            </button>
          </div>
          {note && <p className="text-xs text-muted">{note}</p>}
        </div>
        <div className="min-h-0 flex-1 overflow-auto">
          {shown.map((p) => (
            <button
              key={p.author_id}
              onClick={() => onSelect(p.author_id)}
              className={cx(
                'flex h-11 w-full items-center gap-3 px-5 text-left text-[13.5px]',
                p.author_id === selected ? 'bg-raised text-gray-100 shadow-[inset_2px_0_0_#f3f4f6]' : 'text-gray-300 hover:bg-[#26272d]'
              )}
            >
              <span className="size-2 shrink-0 rounded-full" style={{ background: color(p.color_index) }} />
              <span className="flex-1 truncate">{p.display_name || p.name}</span>
              {p.memories.new > 0 ? (
                <span className="font-mono text-[11.5px] text-[#E0B25C]">{p.memories.new} new</span>
              ) : (
                <span className="font-mono text-[11.5px] text-muted">{p.memories.kept || ''}</span>
              )}
            </button>
          ))}
        </div>
      </nav>

      <main className="min-w-0 flex-[999_1_480px] overflow-auto px-10 pb-12">
        {!person ? (
          <div className="py-24 text-center text-sm text-muted">No people yet.</div>
        ) : (
          <div className="max-w-[860px]">
            <header className="flex flex-wrap items-center gap-4 pt-7 pb-5">
              <span className="size-3 rounded-full" style={{ background: color(person.color_index) }} />
              <div className="min-w-[220px] flex-1">
                <h2 className="text-[22px] font-semibold">{person.display_name || person.name}</h2>
                <p className="mt-1 text-[13px] text-muted">
                  {person.messages} messages · {person.conversations} conversations · {pct(person.share)} of everything
                </p>
              </div>
              {tab === 'new' && mems.length > 0 && (
                <button
                  onClick={async () => {
                    try {
                      await post(`/people/${person.author_id}/memories/keep-all`)
                      refresh()
                    } catch (e) {
                      onError(e)
                    }
                  }}
                  className="rounded-md bg-gray-100 px-3 py-2 text-[13px] font-semibold text-ink hover:bg-white"
                >
                  Keep all new
                </button>
              )}
            </header>

            <div role="tablist" className="flex gap-5 border-b border-[#2f3037]">
              {(['new', 'kept', 'forgotten'] as const).map((t) => (
                <button
                  key={t}
                  role="tab"
                  aria-selected={tab === t}
                  onClick={() => setTab(t)}
                  className={cx('h-10 border-b-2 text-[13px] capitalize', tab === t ? 'border-gray-100 text-gray-100' : 'border-transparent text-muted hover:text-gray-200')}
                >
                  {t} {person.memories[t] ? <span className="font-mono text-[11px] text-muted">{person.memories[t]}</span> : null}
                </button>
              ))}
            </div>

            {tab === 'kept' && (
              <form
                className="flex gap-2 pt-5"
                onSubmit={async (e) => {
                  e.preventDefault()
                  if (!adding.trim()) return
                  try {
                    await post(`/people/${person.author_id}/memories`, { text: adding, category: 'fact' })
                    setAdding('')
                    refresh()
                  } catch (err) {
                    onError(err)
                  }
                }}
              >
                <input
                  value={adding}
                  onChange={(e) => setAdding(e.target.value)}
                  placeholder={`Add something to remember about ${person.display_name || person.name}`}
                  className="h-9 flex-1 rounded-md border border-line bg-transparent px-2.5 text-sm outline-none focus:border-gray-400"
                />
                <button className="rounded-md border border-line px-3 text-sm hover:bg-raised">Add</button>
              </form>
            )}

            {GROUPS.map(([cat, title]) => {
              const list = mems.filter((m) => m.category === cat)
              if (!list.length) return null
              return (
                <section key={cat}>
                  <h3 className="mt-7 border-b border-[#2f3037] pb-2.5 text-[13px] font-semibold text-muted">{title}</h3>
                  {list.map((m) => (
                    <div key={m.id} className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-6 gap-y-1.5 border-b border-[#2f3037] py-4">
                      {editing === m.id ? (
                        <form
                          className="flex gap-2"
                          onSubmit={(e) => {
                            e.preventDefault()
                            update(m, { text: draft })
                            setEditing(null)
                          }}
                        >
                          <input
                            autoFocus
                            value={draft}
                            onChange={(e) => setDraft(e.target.value)}
                            className="h-8 flex-1 rounded border border-line bg-transparent px-2 text-sm outline-none"
                          />
                          <button className="text-sm text-gray-200">Save</button>
                        </form>
                      ) : (
                        <div className={cx('text-[14.5px] leading-snug', m.status === 'forgotten' && 'text-muted')}>
                          {m.text}
                          {m.short_lived && <span className="ml-2 text-xs text-muted">short-lived, probably skip</span>}
                          {m.synced && <span className="ml-2 text-xs text-[#6CCB98]">synced</span>}
                        </div>
                      )}
                      <div className="row-span-2 flex items-center gap-3 text-muted">
                        {m.status !== 'kept' && (
                          <button title="Keep" aria-label="Keep" onClick={() => update(m, { status: 'kept' })} className="text-[#6CCB98] hover:text-white">
                            <LuCheck className="size-[17px]" />
                          </button>
                        )}
                        <button
                          title="Edit"
                          aria-label="Edit"
                          onClick={() => {
                            setEditing(m.id)
                            setDraft(m.text)
                          }}
                          className="hover:text-gray-100"
                        >
                          <LuPencil className="size-4" />
                        </button>
                        {m.category === 'relationship' && (
                          <button title="Link to a person" aria-label="Link to a person" onClick={() => setLinking(linking === m.id ? null : m.id)} className="hover:text-gray-100">
                            <LuLink className="size-4" />
                          </button>
                        )}
                        {m.status === 'forgotten' ? (
                          <button title="Restore" aria-label="Restore" onClick={() => update(m, { status: 'new' })} className="hover:text-gray-100">
                            <LuUndo2 className="size-4" />
                          </button>
                        ) : (
                          <button title="Forget" aria-label="Forget" onClick={() => update(m, { status: 'forgotten' })} className="hover:text-gray-100">
                            <LuTrash2 className="size-4" />
                          </button>
                        )}
                      </div>
                      {m.sources.length > 0 && (
                        <div className="text-[12.5px] text-muted">
                          <span className="font-mono">{fmtTime(m.sources[0].ts)}</span> · “{m.sources[0].content_text}”
                          {m.sources.length > 1 && ` · ${m.sources.length} sources`} ·{' '}
                          <button onClick={() => openMessage(m.sources[0].id)} className="underline underline-offset-2 hover:text-gray-200">
                            open
                          </button>
                        </div>
                      )}
                      {linking === m.id && (
                        <div className="col-span-2 flex flex-wrap gap-1.5 pt-1">
                          {people
                            .filter((p) => p.author_id !== m.author_id)
                            .map((p) => (
                              <button
                                key={p.author_id}
                                onClick={() => {
                                  update(m, { linked_author_id: p.author_id })
                                  setLinking(null)
                                }}
                                className="flex items-center gap-1.5 rounded border border-line px-2 py-1 text-xs hover:bg-raised"
                              >
                                <span className="size-2 rounded-full" style={{ background: color(p.color_index) }} />
                                {p.display_name || p.name}
                              </button>
                            ))}
                        </div>
                      )}
                    </div>
                  ))}
                </section>
              )
            })}
            {!mems.length && (
              <p className="py-10 text-sm text-muted">
                {tab === 'new' ? 'Nothing new. Use “Read messages for memories” to look for more.' : `No ${tab} memories.`}
              </p>
            )}
          </div>
        )}
      </main>
    </div>
  )
}
