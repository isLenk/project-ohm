import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import type { IconType } from 'react-icons'
import {
  LuBadgeCheck,
  LuBookA,
  LuCheck,
  LuCircleDot,
  LuHeart,
  LuLayers,
  LuLink,
  LuMerge,
  LuMessageSquareQuote,
  LuPencil,
  LuSearch,
  LuStar,
  LuTrash2,
  LuUndo2,
  LuX,
  LuZap
} from 'react-icons/lu'
import { get, patch, post, stream } from './api'
import { OpenSource, SourceAside, type Preview, type Source } from './SourcePreview'
import TermsView from './TermsView'
import { useTerms } from './terms'
import { Avatar, BotTag } from './Avatar'
import { cx, fmtWhen, pct } from './util'
import { Loading, Spinner } from '../components/Loader'

type Person = {
  author_id: string
  name: string
  display_name: string | null
  color_index: number
  avatar_url: string | null
  is_bot: boolean
  messages: number
  conversations: number
  share: number
  memories: { new: number; kept: number; forgotten: number }
}

type Priority = 'important' | 'interest' | 'opinion' | 'other' | 'short_lived'

type Memory = {
  id: number
  author_id: string
  category: 'fact' | 'relationship' | 'style'
  text: string
  status: 'new' | 'kept' | 'forgotten'
  short_lived: boolean
  priority: Priority
  /** Id of the first memory in the list that says the same thing (its own id if none does). */
  group: number
  mentioned_name: string | null
  linked_author_id: string | null
  synced: boolean
  sources: Source[]
  /** 0..1, the same for every note in a group: priority, how often it came up, no hedging. */
  confidence?: number
  /** confidence is high enough to keep without reading the messages behind it. */
  sure?: boolean
}

/** Memories that say the same thing, shown as one. `lead` is the one whose wording is shown. */
type Group = { lead: Memory; members: Memory[]; sources: Source[]; priority: Priority; confidence: number; sure: boolean }

const SURE_COLOR = '#6CCB98'
const SURE_HINT = 'Sure bets: who they are or what they keep coming back to, said plainly. Safe to keep without checking.'

const GROUPS: [Memory['category'], string][] = [
  ['fact', 'Facts and habits'],
  ['relationship', 'People they know'],
  ['style', 'How they write']
]

// Best first: a group shows the highest priority any of its notes has.
const PRIORITIES: { key: Priority; label: string; hint: string; icon: IconType; color: string }[] = [
  { key: 'important', label: 'Important', hint: 'Important: who they are (work, school, family, pets, where they live)', icon: LuStar, color: '#E0B25C' },
  { key: 'interest', label: 'Interests', hint: 'Interest: things they like, play or watch', icon: LuHeart, color: '#F47A9B' },
  { key: 'opinion', label: 'Opinions', hint: 'Opinion: what they think or want', icon: LuMessageSquareQuote, color: '#A78BFA' },
  { key: 'other', label: 'Other', hint: 'Other', icon: LuCircleDot, color: '#8b8d97' },
  { key: 'short_lived', label: 'Short-lived', hint: 'Short-lived, probably skip', icon: LuZap, color: '#5EC8E5' }
]
const PRIO = Object.fromEntries(PRIORITIES.map((p, i) => [p.key, { ...p, rank: i }])) as Record<
  Priority,
  (typeof PRIORITIES)[number] & { rank: number }
>

function PriorityIcon({ priority, className = 'size-3.5' }: { priority: Priority; className?: string }) {
  const p = PRIO[priority]
  const Icon = p.icon
  return (
    <span title={p.hint} aria-label={p.hint} role="img" className="inline-flex shrink-0" style={{ color: p.color }}>
      <Icon className={className} />
    </span>
  )
}

function toGroups(mems: Memory[]): Group[] {
  const byLead = new Map<number, Memory[]>()
  for (const m of mems) {
    // An older curator_service sends neither field: show each memory on its own.
    m.group ??= m.id
    m.priority ??= m.short_lived ? 'short_lived' : 'other'
    const list = byLead.get(m.group)
    if (list) list.push(m)
    else byLead.set(m.group, [m])
  }
  return [...byLead.values()].map((members) => {
    // The wording that came up most often (most messages behind it) stands for the group.
    const lead = members.reduce((a, b) => (b.sources.length > a.sources.length ? b : a))
    const seen = new Set<string>()
    const sources = members
      .flatMap((m) => m.sources)
      .filter((s) => !seen.has(s.id) && !!seen.add(s.id))
      .sort((a, b) => a.ts - b.ts)
    const priority = members.reduce<Priority>((best, m) => (PRIO[m.priority].rank < PRIO[best].rank ? m.priority : best), lead.priority)
    return { lead, members, sources, priority, confidence: lead.confidence ?? 0, sure: !!lead.sure }
  })
}

// ── view ─────────────────────────────────────────────────────────────────────
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
  const [prio, setPrio] = useState<Priority | 'all' | 'sure'>('all')
  const [query, setQuery] = useState('')
  const [mems, setMems] = useState<Memory[]>([])
  const [editing, setEditing] = useState<number | null>(null)
  const [draft, setDraft] = useState('')
  const [adding, setAdding] = useState('')
  const [linking, setLinking] = useState<number | null>(null)
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const [preview, setPreview] = useState<Preview | null>(null)
  const [job, setJob] = useState<string | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const [stopping, setStopping] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [peopleLoaded, setPeopleLoaded] = useState(false)
  // Which person and tab `mems` was fetched for; until it matches, the list is still loading.
  const [memsFor, setMemsFor] = useState<string | null>(null)
  const [syncing, setSyncing] = useState(false)
  // The General terminology entry at the top of the list, instead of a person.
  const [general, setGeneral] = useState(false)
  const { counts: termCounts } = useTerms()
  useEffect(() => setGeneral(false), [selected])

  const loadPeople = useCallback(async () => {
    try {
      const r = await get<{ people: Person[] }>('/people')
      setPeople(r.people)
      if (!selected && r.people.length) onSelect(r.people.find((p) => !p.is_bot)?.author_id || r.people[0].author_id)
    } catch (e) {
      onError(e)
    } finally {
      setPeopleLoaded(true)
    }
  }, [selected, onSelect, onError])

  const loadMems = useCallback(async () => {
    if (!selected) return
    try {
      const r = await get<{ memories: Memory[] }>(`/people/${selected}/memories?status=${tab}`)
      setMems(r.memories)
    } catch (e) {
      onError(e)
    } finally {
      setMemsFor(`${selected}:${tab}`)
    }
  }, [selected, tab, onError])

  useEffect(() => {
    loadPeople()
  }, [loadPeople])
  useEffect(() => {
    loadMems()
  }, [loadMems])
  useEffect(() => {
    setPreview(null)
    setExpanded(new Set())
  }, [selected, general])

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

  /** Fold a group into its lead (they say the same thing), then apply `body` to it if given. */
  const actOnGroup = async (g: Group, body?: any) => {
    try {
      const others = g.members.filter((m) => m.id !== g.lead.id && m.author_id === g.lead.author_id).map((m) => m.id)
      if (others.length) await post(`/memories/${g.lead.id}/merge`, { ids: others })
      if (body) await patch(`/memories/${g.lead.id}`, body)
      refresh()
    } catch (e) {
      onError(e)
    }
  }

  // The job runs in curator_service, not here: leaving the tab only stops watching it, and coming back picks it up again.
  const refreshRef = useRef(refresh)
  refreshRef.current = refresh
  const watching = useRef<(() => void) | null>(null)

  const follow = useCallback(
    (id: string) => {
      watching.current?.()
      setJobId(id)
      setJob('Reading messages…')
      let lastRefresh = Date.now()
      watching.current = stream(`/jobs/${id}/events`, (name, data) => {
        if (name === 'closed' || name === 'error') {
          watching.current = null
          setJobId(null)
          setJob(null)
          setStopping(false)
          return
        }
        if (name !== 'progress' || !data) return
        if (data.status === 'running') {
          setJob(data.total ? `Reading messages ${data.done}/${data.total}` : 'Reading messages…')
          // Show memories as they turn up instead of only at the end.
          if (Date.now() - lastRefresh > 4000) {
            lastRefresh = Date.now()
            refreshRef.current()
          }
          return
        }
        watching.current?.()
        watching.current = null
        setJobId(null)
        setJob(null)
        setStopping(false)
        const result = data.result
        if (data.status === 'failed') {
          onError(new Error(data.error || 'Reading messages failed.'))
        } else {
          const found = `Found ${result?.created ?? 0} new memories (${result?.merged ?? 0} matched ones you already had).`
          if (data.status === 'cancelled') setNote(`Stopped. ${found} Run it again to carry on from where it left off.`)
          else if (result?.stopped) setNote(`${result.stopped} ${found} Run it again once it's back to finish.`)
          else if (result?.failed) setNote(`${found} ${result.failed} blocks timed out; run it again to retry them.`)
          else setNote(found)
        }
        refreshRef.current()
      })
    },
    [onError]
  )

  useEffect(() => {
    get<{ jobs: { id: string; kind: string; status: string }[] }>('/jobs')
      .then((r) => {
        const running = r.jobs.find((j) => j.kind === 'memories' && j.status === 'running')
        if (running) follow(running.id)
      })
      .catch(() => {})
    return () => {
      watching.current?.()
      watching.current = null
    }
  }, [follow])

  const extract = async () => {
    try {
      setNote(null)
      const j = await post('/memories/extract', {})
      follow(j.id)
    } catch (e) {
      onError(e)
    }
  }

  const stop = async () => {
    if (!jobId) return
    try {
      setStopping(true)
      await post(`/jobs/${jobId}/cancel`)
    } catch (e) {
      setStopping(false)
      onError(e)
    }
  }

  const sync = async () => {
    setSyncing(true)
    try {
      const r = await post('/memories/sync')
      setNote(
        r.failed
          ? `Synced ${r.synced}, ${r.remaining} still to go. ${r.error || 'The Curator log has the details.'}`
          : `Synced ${r.synced} memories to the memory service.`
      )
      onDirty()
    } catch (e) {
      onError(e)
    } finally {
      setSyncing(false)
    }
  }
  const memsLoading = memsFor !== `${selected}:${tab}`

  const person = people.find((p) => p.author_id === selected)
  const shown = people.filter((p) => (p.display_name || p.name).toLowerCase().includes(filter.toLowerCase()))

  const groups = useMemo(() => toGroups(mems), [mems])
  const searched = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return groups
    return groups.filter((g) => g.members.some((m) => m.text.toLowerCase().includes(q)) || g.sources.some((s) => s.content_text.toLowerCase().includes(q)))
  }, [groups, query])
  const counts = useMemo(() => {
    const c = {} as Record<Priority, number>
    for (const g of searched) c[g.priority] = (c[g.priority] || 0) + 1
    return c
  }, [searched])
  // Sure bets, most certain first, so the top of the list can be kept at a glance.
  const sureBets = useMemo(() => searched.filter((g) => g.sure).sort((a, b) => b.confidence - a.confidence), [searched])
  // What "Keep sure bets" keeps: the person's own notes (not ones linked from someone else), whatever the search says.
  const sureToKeep = useMemo(() => groups.filter((g) => g.sure && g.lead.author_id === selected).length, [groups, selected])
  const visible = prio === 'all' ? searched : prio === 'sure' ? sureBets : searched.filter((g) => g.priority === prio)

  const openPreview = (g: Group, sourceId?: string) =>
    setPreview({
      title: g.lead.text,
      icon: <PriorityIcon priority={g.priority} className="size-4" />,
      sources: g.sources,
      index: Math.max(0, sourceId ? g.sources.findIndex((s) => s.id === sourceId) : g.sources.length - 1)
    })
  const highlightOf = (g: Group) => new Set(g.sources.map((s) => s.id))

  const sourceLine = (g: Group, s: Source, count: number, extra?: ReactNode) => (
    <div className="text-[12.5px] text-muted">
      <span className="font-mono">{fmtWhen(s.ts)}</span> · “{s.content_text}”{count > 1 && ` · ${count} sources`}
      {extra} · <OpenSource source={s} highlight={highlightOf(g)} onOpen={() => openPreview(g, s.id)} />
    </div>
  )

  const renderGroup = (g: Group) => {
    const m = g.lead
    const size = g.members.length
    const open = expanded.has(m.group)
    const previewing = preview?.sources[preview.index] && g.sources.some((s) => s.id === preview.sources[preview.index].id)
    return (
      <div key={m.group} className={cx('border-b border-[#2f3037] py-4', previewing && 'bg-[#1d1e22] -mx-3 px-3')}>
        <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-6 gap-y-1.5">
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
            <div className={cx('flex items-start gap-2 text-[14.5px] leading-snug', m.status === 'forgotten' && 'text-muted')}>
              <PriorityIcon priority={g.priority} className="mt-[3px] size-3.5" />
              <span className="min-w-0">
                {m.text}
                {size > 1 && (
                  <button
                    onClick={() =>
                      setExpanded((s) => {
                        const n = new Set(s)
                        if (n.has(m.group)) n.delete(m.group)
                        else n.add(m.group)
                        return n
                      })
                    }
                    title="Notes that say the same thing"
                    className="ml-2 inline-flex items-center gap-1 rounded border border-line px-1.5 py-px align-[1px] text-[11px] text-muted hover:bg-raised hover:text-gray-200"
                  >
                    <LuLayers className="size-3" /> {size - 1} similar
                  </button>
                )}
                {m.status === 'new' && g.sure && (
                  <span
                    title={`Sure bet (${Math.round(g.confidence * 100)}% sure). ${SURE_HINT}`}
                    className="ml-2 inline-flex items-center gap-1 align-[-1px] text-xs"
                    style={{ color: SURE_COLOR }}
                  >
                    <LuBadgeCheck className="size-3.5" /> sure bet
                  </span>
                )}
                {m.synced && <span className="ml-2 text-xs text-[#6CCB98]">synced</span>}
              </span>
            </div>
          )}
          <div className="row-span-2 flex items-center gap-3 text-muted">
            {m.status !== 'kept' && (
              <button
                title={size > 1 ? 'Keep (as one memory)' : 'Keep'}
                aria-label="Keep"
                onClick={() => actOnGroup(g, { status: 'kept' })}
                className="text-[#6CCB98] hover:text-white"
              >
                <LuCheck className="size-[17px]" />
              </button>
            )}
            {size > 1 && (
              <button title="Merge into one memory" aria-label="Merge into one memory" onClick={() => actOnGroup(g)} className="hover:text-gray-100">
                <LuMerge className="size-4" />
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
              <button title="Restore" aria-label="Restore" onClick={() => actOnGroup(g, { status: 'new' })} className="hover:text-gray-100">
                <LuUndo2 className="size-4" />
              </button>
            ) : (
              <button title={size > 1 ? 'Forget all of these' : 'Forget'} aria-label="Forget" onClick={() => actOnGroup(g, { status: 'forgotten' })} className="hover:text-gray-100">
                <LuTrash2 className="size-4" />
              </button>
            )}
          </div>
          {g.sources.length > 0 && sourceLine(g, g.sources[g.sources.length - 1], g.sources.length)}
        </div>
        {open && (
          <ul className="mt-3 ml-5 flex flex-col gap-2.5 border-l border-[#2f3037] pl-4">
            {g.members
              .filter((x) => x.id !== m.id)
              .map((x) => (
                <li key={x.id} className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-6 gap-y-1">
                  <div className="text-[13.5px] leading-snug text-gray-300">{x.text}</div>
                  <div className="row-span-2 flex items-center gap-3 text-muted">
                    {x.status !== 'kept' && (
                      <button title="Keep this one on its own" aria-label="Keep this one on its own" onClick={() => update(x, { status: 'kept' })} className="hover:text-[#6CCB98]">
                        <LuCheck className="size-4" />
                      </button>
                    )}
                    {x.status !== 'forgotten' && (
                      <button title="Forget this one" aria-label="Forget this one" onClick={() => update(x, { status: 'forgotten' })} className="hover:text-gray-100">
                        <LuTrash2 className="size-3.5" />
                      </button>
                    )}
                  </div>
                  {x.sources.length > 0 && sourceLine(g, x.sources[x.sources.length - 1], x.sources.length)}
                </li>
              ))}
          </ul>
        )}
        {linking === m.id && (
          <div className="flex flex-wrap gap-1.5 pt-2">
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
                  <Avatar who={p} size={16} />
                  {p.display_name || p.name}
                </button>
              ))}
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="relative flex min-h-0 flex-1">
      <nav aria-label="People" className="flex h-full w-[300px] shrink-0 flex-col border-r border-[#2f3037]">
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
            {jobId ? (
              <>
                <span className="flex flex-1 items-center rounded-md border border-line px-2 py-1.5 text-xs text-muted">{stopping ? 'Stopping…' : job}</span>
                <button onClick={stop} disabled={stopping} className="rounded-md border border-line px-2 py-1.5 text-xs hover:bg-raised disabled:opacity-60">
                  Stop
                </button>
              </>
            ) : (
              <button onClick={extract} className="flex-1 rounded-md border border-line px-2 py-1.5 text-xs hover:bg-raised">
                Read messages for memories
              </button>
            )}
            <button
              onClick={sync}
              disabled={syncing}
              className="flex items-center gap-1.5 rounded-md border border-line px-2 py-1.5 text-xs hover:bg-raised disabled:opacity-60"
            >
              {syncing && <Spinner className="size-3.5" />}
              {syncing ? 'Syncing…' : 'Sync kept'}
            </button>
          </div>
          {note && <p className="text-xs text-muted">{note}</p>}
        </div>
        <div className="min-h-0 flex-1 overflow-auto">
          <button
            onClick={() => setGeneral(true)}
            className={cx(
              'flex h-11 w-full items-center gap-3 border-b border-[#2f3037] px-5 text-left text-[13.5px]',
              general ? 'bg-raised text-gray-100 shadow-[inset_2px_0_0_#f3f4f6]' : 'text-gray-300 hover:bg-[#26272d]'
            )}
          >
            <span className="grid size-[22px] place-items-center rounded-full bg-[#26272d]">
              <LuBookA className="size-3.5" />
            </span>
            <span className="truncate">General terminology</span>
            <span className="flex-1" />
            {termCounts.unknown > 0 && (
              <span title="Words Ohm doesn't know yet" className="font-mono text-[11.5px] text-[#E0B25C]">
                {termCounts.unknown} ?
              </span>
            )}
          </button>
          {!peopleLoaded && <Loading label="Loading people…" />}
          {shown.map((p) => (
            <button
              key={p.author_id}
              onClick={() => {
                setGeneral(false)
                onSelect(p.author_id)
              }}
              className={cx(
                'flex h-11 w-full items-center gap-3 px-5 text-left text-[13.5px]',
                !general && p.author_id === selected ? 'bg-raised text-gray-100 shadow-[inset_2px_0_0_#f3f4f6]' : 'text-gray-300 hover:bg-[#26272d]'
              )}
            >
              <Avatar who={p} size={22} />
              <span className="truncate">{p.display_name || p.name}</span>
              {p.is_bot && <BotTag />}
              <span className="flex-1" />
              {p.memories.new > 0 ? (
                <span className="font-mono text-[11.5px] text-[#E0B25C]">{p.memories.new} new</span>
              ) : (
                <span className="font-mono text-[11.5px] text-muted">{p.memories.kept || ''}</span>
              )}
            </button>
          ))}
        </div>
      </nav>

      <main className="h-full min-w-0 flex-1 overflow-auto px-10 pb-12">
        {general ? (
          <TermsView onDirty={onDirty} onError={onError} onPreview={setPreview} />
        ) : !person ? (
          peopleLoaded ? (
            <div className="py-24 text-center text-sm text-muted">No people yet.</div>
          ) : null
        ) : (
          <div className="max-w-[860px]">
            <header className="flex flex-wrap items-center gap-4 pt-7 pb-5">
              <Avatar who={person} size={44} />
              <div className="min-w-[220px] flex-1">
                <h2 className="flex items-center gap-2.5 text-[22px] font-semibold">
                  {person.display_name || person.name}
                  {person.is_bot && <BotTag />}
                </h2>
                <p className="mt-1 text-[13px] text-muted">
                  {person.messages} messages · {person.conversations} conversations · {pct(person.share)} of everything
                </p>
              </div>
              {tab === 'new' && sureToKeep > 0 && (
                <button
                  title={`${SURE_HINT} Notes that say the same thing are kept as one.`}
                  onClick={async () => {
                    try {
                      await post(`/people/${person.author_id}/memories/keep-all?sure=true`)
                      refresh()
                    } catch (e) {
                      onError(e)
                    }
                  }}
                  className="flex items-center gap-1.5 rounded-md bg-gray-100 px-3 py-2 text-[13px] font-semibold text-ink hover:bg-white"
                >
                  <LuBadgeCheck className="size-4" />
                  Keep {sureToKeep} sure {sureToKeep === 1 ? 'bet' : 'bets'}
                </button>
              )}
              {tab === 'new' && mems.length > 0 && (
                <button
                  title="Keeps everything that isn't short-lived. Notes that say the same thing are kept as one."
                  onClick={async () => {
                    try {
                      await post(`/people/${person.author_id}/memories/keep-all`)
                      refresh()
                    } catch (e) {
                      onError(e)
                    }
                  }}
                  className={cx(
                    'rounded-md px-3 py-2 text-[13px]',
                    sureToKeep > 0 ? 'border border-line hover:bg-raised' : 'bg-gray-100 font-semibold text-ink hover:bg-white'
                  )}
                >
                  Keep all new
                </button>
              )}
            </header>

            <div className="flex flex-wrap items-end gap-x-5 border-b border-[#2f3037]">
              <div role="tablist" aria-label="Status" className="flex gap-5">
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
              <span className="mb-2.5 h-4 w-px bg-[#2f3037]" aria-hidden />
              <div role="tablist" aria-label="Priority" className="flex gap-1">
                <button
                  role="tab"
                  aria-selected={prio === 'all'}
                  onClick={() => setPrio('all')}
                  className={cx('h-10 border-b-2 px-1.5 text-[13px]', prio === 'all' ? 'border-gray-100 text-gray-100' : 'border-transparent text-muted hover:text-gray-200')}
                >
                  All
                </button>
                <button
                  role="tab"
                  aria-selected={prio === 'sure'}
                  aria-label="Sure bets"
                  title={`${SURE_HINT} Most certain first.`}
                  onClick={() => setPrio(prio === 'sure' ? 'all' : 'sure')}
                  className={cx(
                    'flex h-10 items-center gap-1.5 border-b-2 px-1.5 text-[13px]',
                    prio === 'sure' ? 'border-gray-100 text-gray-100' : 'border-transparent text-muted hover:text-gray-200'
                  )}
                >
                  <LuBadgeCheck className="size-[15px]" style={{ color: SURE_COLOR }} />
                  {prio === 'sure' && <span>Sure bets</span>}
                  <span className="font-mono text-[11px] text-muted">{sureBets.length}</span>
                </button>
                {PRIORITIES.map((p) => {
                  const Icon = p.icon
                  const on = prio === p.key
                  return (
                    <button
                      key={p.key}
                      role="tab"
                      aria-selected={on}
                      aria-label={p.label}
                      title={p.hint}
                      onClick={() => setPrio(on ? 'all' : p.key)}
                      className={cx(
                        'flex h-10 items-center gap-1.5 border-b-2 px-1.5 text-[13px]',
                        on ? 'border-gray-100 text-gray-100' : 'border-transparent text-muted hover:text-gray-200'
                      )}
                    >
                      <Icon className="size-[15px]" style={{ color: p.color }} />
                      {on && <span>{p.label}</span>}
                      <span className="font-mono text-[11px] text-muted">{counts[p.key] || 0}</span>
                    </button>
                  )
                })}
              </div>
            </div>

            <label className="mt-4 flex h-9 items-center gap-2 rounded-md border border-line px-2.5 focus-within:border-gray-400">
              <LuSearch className="size-4 shrink-0 text-muted" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => e.key === 'Escape' && setQuery('')}
                placeholder={`Search ${person.display_name || person.name}’s memories and the messages behind them`}
                aria-label="Search memories"
                className="min-w-0 flex-1 bg-transparent text-sm outline-none"
              />
              {query && (
                <button type="button" title="Clear" aria-label="Clear search" onClick={() => setQuery('')} className="text-muted hover:text-gray-100">
                  <LuX className="size-4" />
                </button>
              )}
            </label>

            {tab === 'kept' && (
              <form
                className="flex gap-2 pt-3"
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

            {memsLoading && <Loading label="Loading memories…" />}
            {!memsLoading &&
              GROUPS.map(([cat, title]) => {
                const list = visible.filter((g) => g.lead.category === cat)
                if (!list.length) return null
                return (
                  <section key={cat}>
                    <h3 className="mt-7 border-b border-[#2f3037] pb-2.5 text-[13px] font-semibold text-muted">{title}</h3>
                    {list.map(renderGroup)}
                  </section>
                )
              })}
            {!memsLoading && !visible.length && (
              <p className="py-10 text-sm text-muted">
                {mems.length
                  ? prio === 'sure' && !query.trim()
                    ? 'No sure bets here. The rest need a look before keeping.'
                    : 'Nothing matches.'
                  : tab === 'new'
                    ? 'Nothing new. Use “Read messages for memories” to look for more.'
                    : `No ${tab} memories.`}
              </p>
            )}
          </div>
        )}
      </main>

      <SourceAside
        preview={preview}
        onIndex={(index) => setPreview((p) => (p ? { ...p, index } : p))}
        onClose={() => setPreview(null)}
        openMessage={openMessage}
      />
    </div>
  )
}
