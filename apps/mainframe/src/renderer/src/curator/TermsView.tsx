import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { LuBookA, LuCheck, LuEye, LuEyeOff, LuPencil, LuSearch, LuSparkles, LuTrash2, LuX } from 'react-icons/lu'
import { del, get, post, stream } from './api'
import { OpenSource, type Preview } from './SourcePreview'
import { TermEditor, TermIcon } from './TermPicker'
import {
  acceptAllSuggestions,
  acceptSuggestion,
  dismissSuggestion,
  hasSuggestion,
  loadTerms,
  reloadAfterShare,
  shareTerms,
  updateTerm,
  useTerms,
  withSuggestion,
  type Suggestion,
  type Term,
  type TermStatus
} from './terms'
import { cx, fmtWhen } from './util'
import { Loading, Spinner } from '../components/Loader'

const TABS: TermStatus[] = ['unknown', 'defined', 'linked', 'ignored']
const TAB_LABEL: Record<TermStatus, string> = { unknown: 'Unknown', defined: 'Defined', linked: 'Typos & same meaning', ignored: 'Normal words' }
const PAGE = 100
const SUGGEST_LABEL = { slang: 'slang', name: 'a name', thing: 'a thing or place' } as const

function SuggestionText({ s }: { s: Suggestion }) {
  if (s.kind === 'definition')
    return (
      <>
        {s.definition}
        {s.label && <span className="ml-1.5 text-muted">({SUGGEST_LABEL[s.label]})</span>}
      </>
    )
  if (s.kind === 'typo' || s.kind === 'same')
    return (
      <>
        {s.kind === 'typo' ? 'Typo of' : 'Same meaning as'} <span className="font-semibold text-gray-100">{s.related_term}</span>
        {s.meaning && s.meaning.toLowerCase() !== s.related_term && <span className="text-muted"> ({s.meaning})</span>}
      </>
    )
  if (s.kind === 'normal') return <>A normal word{s.meaning && <span className="text-muted">: {s.meaning}</span>}</>
  return null
}

/** The term in a message, marked so it's easy to spot. Matches whole words, any case. */
function Marked({ text, term }: { text: string; term: string }) {
  const esc = term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const parts = text.split(new RegExp(`(?<![\\p{L}\\p{N}])(${esc}s?)(?![\\p{L}\\p{N}])`, 'iu'))
  return (
    <>
      {parts.map((p, i) => (i % 2 ? <mark key={i} className="rounded bg-[#E0B25C]/20 px-0.5 text-gray-100">{p}</mark> : p))}
    </>
  )
}

export default function TermsView({ onDirty, onError, onPreview }: {
  onDirty: () => void
  onError: (e: unknown) => void
  onPreview: (p: Preview) => void
}) {
  const { terms, counts, shared, loaded } = useTerms(true)
  const [sharing, setSharing] = useState(false)
  const [tab, setTab] = useState<TermStatus>('unknown')
  const [query, setQuery] = useState('')
  const [editing, setEditing] = useState<number | null>(null)
  const [adding, setAdding] = useState('')
  const [addingFor, setAddingFor] = useState<string | null>(null)
  const [scanning, setScanning] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [limit, setLimit] = useState(PAGE)
  const [job, setJob] = useState<{ id: string; label: string } | null>(null)
  const [stopping, setStopping] = useState(false)
  const [onlySuggested, setOnlySuggested] = useState(false)
  const suggested = useMemo(() => terms.filter(hasSuggestion).length, [terms])
  const waiting = useMemo(() => terms.filter((t) => t.status === 'unknown' && !t.suggestion).length, [terms])

  // Suggesting runs in curator_service: leaving the tab only stops watching it, and coming back picks it up.
  const watching = useRef<(() => void) | null>(null)
  const follow = useCallback(
    (id: string) => {
      watching.current?.()
      setJob({ id, label: 'Suggesting meanings…' })
      let lastLoad = Date.now()
      const finish = () => {
        watching.current?.()
        watching.current = null
        setJob(null)
        setStopping(false)
        loadTerms().catch(() => {})
      }
      watching.current = stream(`/jobs/${id}/events`, (name, data) => {
        if (name === 'closed' || name === 'error') return finish()
        if (name !== 'progress' || !data) return
        if (data.status === 'running') {
          setJob({ id, label: data.total ? `Suggesting meanings ${data.done}/${data.total}` : 'Suggesting meanings…' })
          // Show suggestions as they turn up, so they can be approved or fixed while the rest are worked out.
          // Rows being edited keep what's typed: they stay mounted, keyed by id.
          if (Date.now() - lastLoad > 3000) {
            lastLoad = Date.now()
            loadTerms().catch(() => {})
          }
          return
        }
        const r = data.result
        if (data.status === 'failed') onError(new Error(data.error || 'Suggesting meanings failed.'))
        else if (r) {
          const n = `Suggested meanings for ${r.suggested - (r.kinds?.unsure || 0)} words (unsure about ${r.kinds?.unsure || 0}).`
          setNote(data.status === 'cancelled' ? `Stopped. ${n} Run it again to carry on.` : r.stopped ? `${r.stopped} ${n}` : n)
          if (r.suggested) onDirty()
        }
        finish()
      })
    },
    [onError, onDirty]
  )
  useEffect(() => {
    get<{ jobs: { id: string; kind: string; status: string }[] }>('/jobs')
      .then((r) => {
        const running = r.jobs.find((j) => j.kind === 'terms' && j.status === 'running')
        if (running) follow(running.id)
      })
      .catch(() => {})
    return () => {
      watching.current?.()
      watching.current = null
    }
  }, [follow])

  const suggest = async () => {
    try {
      setNote(null)
      const j = await post<{ id: string }>('/terms/suggest', {})
      follow(j.id)
    } catch (e) {
      onError(e)
    }
  }
  const stop = async () => {
    if (!job) return
    setStopping(true)
    try {
      await post(`/jobs/${job.id}/cancel`)
    } catch (e) {
      setStopping(false)
      onError(e)
    }
  }

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    return terms.filter(
      (t) =>
        t.status === tab &&
        (!onlySuggested || tab !== 'unknown' || hasSuggestion(t)) &&
        (!q || t.term.includes(q) || t.definition?.toLowerCase().includes(q) || t.related_term?.includes(q) || t.examples.some((x) => x.content_text.toLowerCase().includes(q)))
    )
  }, [terms, tab, query, onlySuggested])

  const run = async (fn: () => Promise<unknown>) => {
    try {
      await fn()
      onDirty()
    } catch (e) {
      onError(e)
    }
  }

  const scan = async () => {
    setScanning(true)
    setNote(null)
    try {
      const r = await post<{ found: number; added: number; removed: number }>('/terms/scan')
      await loadTerms()
      onDirty()
      setNote(
        r.added
          ? `Found ${r.added} new unfamiliar ${r.added === 1 ? 'word' : 'words'} (${r.found} in all).`
          : `Nothing new. ${r.found} unfamiliar words in all.`
      )
    } catch (e) {
      onError(e)
    } finally {
      setScanning(false)
    }
  }

  const describe = (t: Term): ReactNode => {
    if (t.related_term) {
      return (
        <>
          {t.relation === 'typo' ? 'Typo of' : 'Same meaning as'} <span className="font-semibold text-gray-200">{t.related_term}</span>
          {t.related_definition && <span className="text-muted">: {t.related_definition}</span>}
        </>
      )
    }
    if (t.definition) return t.definition
    return null
  }

  return (
    <div className="max-w-[860px]">
      <header className="flex flex-wrap items-center gap-4 pt-7 pb-5">
        <span className="grid size-11 place-items-center rounded-full bg-raised text-gray-200">
          <LuBookA className="size-5" />
        </span>
        <div className="min-w-[220px] flex-1">
          <h2 className="text-[22px] font-semibold">General terminology</h2>
          <p className="mt-1 text-[13px] text-muted">
            Words this group uses that Ohm might not know. Ctrl+click any word in the Curator to define it. Definitions go to Ohm, so he understands them in chat and voice, and to the model when it reads messages for memories, in every project.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {job ? (
            <>
              <span className="flex items-center gap-1.5 rounded-md border border-line px-3 py-2 text-[13px] text-muted">
                <Spinner className="size-3.5" />
                {stopping ? 'Stopping…' : job.label}
              </span>
              <button onClick={stop} disabled={stopping} className="rounded-md border border-line px-3 py-2 text-[13px] hover:bg-raised disabled:opacity-60">
                Stop
              </button>
            </>
          ) : (
            waiting > 0 && (
              <button
                onClick={suggest}
                title="Ohm's model reads the messages each unknown word is in and guesses what it means. Nothing changes until you approve a guess."
                className="flex items-center gap-1.5 rounded-md border border-line px-3 py-2 text-[13px] hover:bg-raised"
              >
                <LuSparkles className="size-4" />
                Suggest meanings for {waiting}
              </button>
            )
          )}
          <button
            onClick={scan}
            disabled={scanning}
            className="flex items-center gap-1.5 rounded-md bg-gray-100 px-3 py-2 text-[13px] font-semibold text-ink hover:bg-white disabled:opacity-60"
          >
            {scanning && <Spinner className="size-3.5" />}
            {scanning ? 'Looking…' : 'Look for unfamiliar words'}
          </button>
        </div>
      </header>
      {note && <p className="-mt-2 pb-3 text-xs text-muted">{note}</p>}
      {counts.defined + counts.linked > 0 && (
        <p className={cx('-mt-2 flex flex-wrap items-center gap-x-2 pb-3 text-xs', shared && !shared.ok ? 'text-[#E0B25C]' : 'text-muted')}>
          {!shared
            ? 'Not shared with Ohm yet.'
            : shared.ok
              ? `Ohm knows ${shared.terms} ${shared.terms === 1 ? 'word' : 'words'} from this project.`
              : `Not shared with Ohm: ${shared.error}`}
          {(!shared || !shared.ok) && (
            <button
              disabled={sharing}
              onClick={async () => {
                setSharing(true)
                try {
                  await shareTerms()
                } catch (e) {
                  onError(e)
                } finally {
                  setSharing(false)
                }
              }}
              className="flex items-center gap-1 rounded border border-line px-1.5 py-px text-muted hover:bg-raised hover:text-gray-200 disabled:opacity-60"
            >
              {sharing && <Spinner className="size-3" />}
              {sharing ? 'Sharing…' : 'Share now'}
            </button>
          )}
        </p>
      )}

      <div role="tablist" aria-label="Terms" className="flex flex-wrap gap-5 border-b border-[#2f3037]">
        {TABS.map((t) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            onClick={() => {
              setTab(t)
              setLimit(PAGE)
            }}
            className={cx(
              'flex h-10 items-center gap-1.5 border-b-2 text-[13px]',
              tab === t ? 'border-gray-100 text-gray-100' : 'border-transparent text-muted hover:text-gray-200'
            )}
          >
            <TermIcon status={t} className="size-[15px]" />
            {TAB_LABEL[t]}
            {counts[t] ? <span className="font-mono text-[11px] text-muted">{counts[t]}</span> : null}
          </button>
        ))}
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        <label className="flex h-9 min-w-[240px] flex-1 items-center gap-2 rounded-md border border-line px-2.5 focus-within:border-gray-400">
          <LuSearch className="size-4 shrink-0 text-muted" />
          <input
            value={query}
            onChange={(e) => {
              setQuery(e.target.value)
              setLimit(PAGE)
            }}
            onKeyDown={(e) => e.key === 'Escape' && setQuery('')}
            placeholder="Search terms, definitions and examples"
            aria-label="Search terms"
            className="min-w-0 flex-1 bg-transparent text-sm outline-none"
          />
          {query && (
            <button type="button" title="Clear" aria-label="Clear search" onClick={() => setQuery('')} className="text-muted hover:text-gray-100">
              <LuX className="size-4" />
            </button>
          )}
        </label>
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            if (adding.trim()) setAddingFor(adding.trim())
          }}
        >
          <input
            value={adding}
            onChange={(e) => setAdding(e.target.value)}
            placeholder="Add a word or phrase"
            aria-label="Add a term"
            className="h-9 w-[200px] rounded-md border border-line bg-transparent px-2.5 text-sm outline-none focus:border-gray-400"
          />
          <button className="rounded-md border border-line px-3 text-sm hover:bg-raised">Add</button>
        </form>
      </div>
      {addingFor && (
        <div className="mt-3 rounded-lg border border-line p-3.5">
          <p className="mb-2.5 text-[14px] font-semibold text-gray-100">{addingFor}</p>
          <TermEditor
            term={addingFor}
            initial={terms.find((t) => t.term === addingFor.toLowerCase()) ?? null}
            onCancel={() => setAddingFor(null)}
            onError={onError}
            onSaved={(t) => {
              onDirty()
              setAddingFor(null)
              setAdding('')
              setTab(t.status)
            }}
          />
        </div>
      )}

      {tab === 'unknown' && suggested > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-3 rounded-lg border border-line px-3.5 py-2.5 text-[13px]">
          <LuSparkles className="size-4 shrink-0 text-[#A78BFA]" />
          <span className="flex-1 text-gray-300">
            {suggested} {suggested === 1 ? 'word has a' : 'words have a'} suggested meaning. Approve the right ones, fix or dismiss the rest.
          </span>
          <label className="flex items-center gap-1.5 text-muted">
            <input type="checkbox" checked={onlySuggested} onChange={(e) => setOnlySuggested(e.target.checked)} />
            Only these
          </label>
          <button
            title="Approves every suggestion still waiting, wrong ones too. Skim them first."
            onClick={() =>
              run(async () => {
                const n = await acceptAllSuggestions()
                setNote(`Approved ${n} suggested ${n === 1 ? 'meaning' : 'meanings'}.`)
              })
            }
            className="rounded-md border border-line px-2.5 py-1 hover:bg-raised"
          >
            Approve all {suggested}
          </button>
        </div>
      )}

      {!loaded && <Loading label="Loading terms…" />}
      {loaded && !shown.length && (
        <p className="py-10 text-sm text-muted">
          {query
            ? 'Nothing matches.'
            : tab === 'unknown'
              ? terms.length
                ? 'Every unfamiliar word has been dealt with.'
                : 'No terms yet. Use “Look for unfamiliar words”, or Ctrl+click a word anywhere in the Curator.'
              : `No ${TAB_LABEL[tab].toLowerCase()} yet.`}
        </p>
      )}

      {shown.slice(0, limit).map((t) => (
        <div key={t.id} className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-6 gap-y-1.5 border-b border-[#2f3037] py-4">
          <div className="flex items-baseline gap-2.5">
            <TermIcon status={t.status} className="size-4 translate-y-[3px]" />
            <span className="text-[15px] font-semibold text-gray-100">{t.term}</span>
            {t.uses > 0 && (
              <span className="font-mono text-[11.5px] text-muted">
                {t.uses}× · {t.people} {t.people === 1 ? 'person' : 'people'}
              </span>
            )}
            {t.origin === 'manual' && <span className="text-[11.5px] text-muted">added by you</span>}
          </div>
          <div className="row-span-3 flex items-start gap-3 pt-1 text-muted">
            {hasSuggestion(t) && (
              <button
                title="Approve the suggested meaning"
                aria-label="Approve the suggested meaning"
                onClick={() => run(() => acceptSuggestion(t.id))}
                className="text-[#6CCB98] hover:text-white"
              >
                <LuCheck className="size-[17px]" />
              </button>
            )}
            <button
              title={t.status === 'unknown' ? 'Define, or mark as a typo / same meaning' : 'Edit'}
              aria-label="Edit"
              onClick={() => setEditing(editing === t.id ? null : t.id)}
              className="hover:text-gray-100"
            >
              <LuPencil className="size-4" />
            </button>
            {t.ignored ? (
              <button title="Not a normal word after all" aria-label="Not a normal word" onClick={() => run(() => updateTerm(t.id, { ignored: false }))} className="hover:text-gray-100">
                <LuEye className="size-4" />
              </button>
            ) : (
              <button title="It's a normal word; don't ask" aria-label="Normal word" onClick={() => run(() => updateTerm(t.id, { ignored: true }))} className="hover:text-gray-100">
                <LuEyeOff className="size-4" />
              </button>
            )}
            {t.origin === 'manual' && (
              <button
                title="Delete"
                aria-label="Delete"
                onClick={() =>
                  run(async () => {
                    await del(`/terms/${t.id}`)
                    await loadTerms()
                    reloadAfterShare()
                  })
                }
                className="hover:text-gray-100"
              >
                <LuTrash2 className="size-4" />
              </button>
            )}
          </div>
          {editing === t.id ? (
            <div className="pt-1">
              <TermEditor
                term={t.term}
                initial={withSuggestion(t)}
                onCancel={() => setEditing(null)}
                onError={onError}
                onSaved={() => {
                  onDirty()
                  setEditing(null)
                }}
              />
            </div>
          ) : hasSuggestion(t) ? (
            <div className="flex items-start gap-2 pl-[26px] text-[13.5px] leading-snug text-gray-300">
              <LuSparkles className="mt-[3px] size-3.5 shrink-0 text-[#A78BFA]" aria-label="Suggested" />
              <span className="min-w-0">
                <SuggestionText s={t.suggestion!} />
              </span>
              <button
                title="Wrong; dismiss the suggestion"
                aria-label="Dismiss the suggestion"
                onClick={() => run(() => dismissSuggestion(t.id))}
                className="mt-[2px] shrink-0 text-muted hover:text-gray-100"
              >
                <LuX className="size-3.5" />
              </button>
            </div>
          ) : (
            describe(t) && <div className="pl-[26px] text-[13.5px] leading-snug text-gray-300">{describe(t)}</div>
          )}
          {t.examples.length > 0 && (
            <ul className="flex flex-col gap-1 pl-[26px]">
              {t.examples.map((x) => {
                const sources = t.examples.map((e) => ({ id: e.id, ts: e.ts, content_text: e.content_text }))
                const source = sources.find((s) => s.id === x.id)!
                return (
                  <li key={x.id} className="truncate text-[12.5px] text-muted">
                    <span className="font-mono">{fmtWhen(x.ts)}</span> · <span className="text-gray-400">{x.who}</span>: “
                    <Marked text={x.content_text} term={t.term} />” ·{' '}
                    <OpenSource
                      source={source}
                      highlight={new Set([x.id])}
                      onOpen={() =>
                        onPreview({
                          title: t.term,
                          icon: <TermIcon status={t.status} />,
                          sources,
                          index: sources.indexOf(source)
                        })
                      }
                    />
                  </li>
                )
              })}
            </ul>
          )}
        </div>
      ))}
      {shown.length > limit && (
        <button onClick={() => setLimit((l) => l + PAGE)} className="mt-4 rounded-md border border-line px-3 py-1.5 text-xs text-muted hover:bg-raised hover:text-gray-200">
          Show {Math.min(PAGE, shown.length - limit)} more of {shown.length - limit}
        </button>
      )}
    </div>
  )
}
