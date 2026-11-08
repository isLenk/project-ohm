import { useEffect, useMemo, useRef, useState } from 'react'
import { LuArchive, LuChevronDown, LuChevronRight, LuExternalLink, LuShuffle, LuUndo2, LuX } from 'react-icons/lu'
import { get, type Author, type Candidate, type Candidates, type Criteria } from './api'
import { Avatar } from './Avatar'
import { useAround } from './SourcePreview'
import { REPLIES_AIM, REPLIES_LOW, aimHint, cx, fmtDay, fmtRange, fmtTime, pct } from './util'
import { Loading, Spinner } from '../components/Loader'

// "Find good conversations": the service proposes conversations that look like real chat
// (GET /conversations/candidates), you skim a random sample and reject (archive) the bad ones,
// then approve enough of the rest in one go to reach a reply target. One at a time took an evening
// for ~270 replies; a LoRA wants 1,500 or more.

type Props = {
  /** Kept mounted but out of sight while you look at a sampled conversation in the feed. */
  hidden: boolean
  authors: Record<string, Author>
  onClose: () => void
  /** Show this conversation in the feed (the panel hides until you come back). */
  onOpen: (c: Candidate) => void
  /** Reject = archive. Archived conversations aren't candidates any more. */
  onReject: (id: number) => Promise<void>
  /** Approve these; resolves with a function that takes it back. */
  onApprove: (ids: number[]) => Promise<{ count: number; undo: () => Promise<void> }>
  onError: (e: unknown) => void
}

// Each criterion as a sentence; `scale` turns what you type into the service's unit.
const FIELDS: { key: keyof Criteria; before: string; after: string; scale: number }[] = [
  { key: 'min_messages', before: 'At least', after: 'messages', scale: 1 },
  { key: 'min_people', before: 'At least', after: 'people (bots don’t count)', scale: 1 },
  { key: 'min_repliers', before: 'At least', after: 'people who’d get a reply trained', scale: 1 },
  { key: 'max_gap_ms', before: 'No silence over', after: 'minutes inside it', scale: 60_000 },
  { key: 'max_media', before: 'At most', after: '% of messages only a link or with a file', scale: 0.01 },
  { key: 'min_attached', before: 'At least', after: '% of replies have an arrow', scale: 0.01 }
]
type Draft = Partial<Record<keyof Criteria, string>>
const DRAFT_KEY = 'curator.findCriteria'
const AIMS = [REPLIES_AIM, 3000, 6000] as const
const SAMPLE = 10

const show = (v: number, scale: number) => String(Math.round((v / scale) * 100) / 100)

function query(draft: Draft) {
  const q = new URLSearchParams()
  for (const f of FIELDS) {
    const raw = draft[f.key]?.trim()
    const n = raw ? Number(raw) : NaN
    if (raw && isFinite(n) && n >= 0) q.set(f.key, String(n * f.scale))
  }
  return q.toString()
}

export default function FindGood({ hidden, authors, onClose, onOpen, onReject, onApprove, onError }: Props) {
  const [draft, setDraft] = useState<Draft>(() => {
    try {
      return JSON.parse(localStorage.getItem(DRAFT_KEY) || '{}')
    } catch {
      return {}
    }
  })
  const [data, setData] = useState<Candidates | null>(null)
  const [loading, setLoading] = useState(false)
  const [reload, setReload] = useState(0)
  const [aim, setAim] = useState<number | 'all'>(3000)
  const [sampleSize, setSampleSize] = useState(SAMPLE)
  const [open, setOpen] = useState<number | null>(null)
  const [rejecting, setRejecting] = useState<number | null>(null)
  const [rejected, setRejected] = useState(0)
  const [approving, setApproving] = useState(false)
  const [done, setDone] = useState<{ count: number; replies: number; undo: () => Promise<void> } | null>(null)
  const [undoing, setUndoing] = useState(false)
  // A random rank per conversation, kept across refetches so the sample doesn't jump when a
  // criterion changes or one is rejected. Shuffle draws new ranks.
  const rank = useRef(new Map<number, number>())
  const [shuffled, setShuffled] = useState(0)
  const latest = useRef(0)

  const q = query(draft)
  useEffect(() => {
    localStorage.setItem(DRAFT_KEY, JSON.stringify(draft))
  }, [draft])

  // Refetch when the criteria change (after typing settles) and whenever the panel comes back,
  // since splits, merges and archives in the feed change what passes.
  useEffect(() => {
    if (hidden) return
    const n = ++latest.current
    setLoading(true)
    const t = setTimeout(
      () => {
        get<Candidates>(`/conversations/candidates${q ? `?${q}` : ''}`)
          .then((r) => n === latest.current && setData(r))
          .catch((e) => n === latest.current && onError(e))
          .finally(() => n === latest.current && setLoading(false))
      },
      data ? 350 : 0
    )
    return () => clearTimeout(t)
  }, [q, hidden, reload]) // eslint-disable-line react-hooks/exhaustive-deps

  const ordered = useMemo(() => {
    if (!data) return []
    const r = (id: number) => {
      let v = rank.current.get(id)
      if (v == null) rank.current.set(id, (v = Math.random()))
      return v
    }
    return [...data.candidates].sort((a, b) => r(a.id) - r(b.id))
  }, [data, shuffled])

  // Enough conversations, in sample order, to bring the approved total up to the aim.
  const pick = useMemo(() => {
    if (!data || aim === 'all') return ordered
    const out: Candidate[] = []
    let total = data.approved.replies
    for (const c of ordered) {
      if (total >= aim) break
      out.push(c)
      total += c.replies
    }
    return out
  }, [ordered, aim, data])
  const pickReplies = pick.reduce((n, c) => n + c.replies, 0)
  const sample = pick.slice(0, sampleSize)

  const skippedTotal = data ? Object.values(data.skipped).reduce((a, b) => a + b, 0) : 0
  const approvedReplies = data?.approved.replies ?? 0
  const customised = FIELDS.some((f) => draft[f.key]?.trim())

  const reject = async (c: Candidate) => {
    setRejecting(c.id)
    try {
      await onReject(c.id)
      setRejected((n) => n + 1)
      // Drop it here at once; the next refetch agrees.
      setData((d) => d && { ...d, candidates: d.candidates.filter((x) => x.id !== c.id), replies: d.replies - c.replies })
      if (open === c.id) setOpen(null)
    } catch (e) {
      onError(e)
    } finally {
      setRejecting(null)
    }
  }

  const approve = async () => {
    setApproving(true)
    try {
      const r = await onApprove(pick.map((c) => c.id))
      setDone({ count: r.count, replies: pickReplies, undo: r.undo })
      setOpen(null)
      setReload((n) => n + 1)
    } catch (e) {
      onError(e)
    } finally {
      setApproving(false)
    }
  }

  const undo = async () => {
    if (!done) return
    setUndoing(true)
    try {
      await done.undo()
      setDone(null)
      setReload((n) => n + 1)
    } catch (e) {
      onError(e)
    } finally {
      setUndoing(false)
    }
  }

  return (
    <div className={cx('fixed inset-0 z-50 grid place-items-center bg-black/55 p-4', hidden && 'hidden')}>
      <div role="dialog" aria-labelledby="find-title" className="flex max-h-[min(860px,94vh)] w-[720px] max-w-full flex-col rounded-lg border border-[#3a3b42] bg-[#1d1e22]">
        <div className="flex items-start gap-3 border-b border-[#2f3037] px-6 pt-5 pb-4">
          <div className="flex-1">
            <h2 id="find-title" className="text-[17px] font-semibold">
              Find good conversations
            </h2>
            <p className="mt-1.5 text-[12.5px] leading-relaxed text-muted">
              Conversations that look like real chat. Skim the sample, reject the bad ones, then approve the rest at once. Approving accepts their predicted arrows.
            </p>
          </div>
          <button aria-label="Close" onClick={onClose} className="grid size-8 place-items-center rounded text-muted hover:text-gray-100">
            <LuX className="size-5" />
          </button>
        </div>

        <div className="flex min-h-0 flex-1 flex-col gap-5 overflow-auto px-6 py-5 text-sm">
          {/* progress toward a useful training set */}
          <div>
            <div className="flex items-baseline justify-between text-[13px]">
              <span className={cx(data && approvedReplies < REPLIES_LOW ? 'text-[#E0B25C]' : 'text-gray-200')}>
                {data ? `About ${aimHint(approvedReplies)}` : 'Counting approved replies…'}
              </span>
              <span className="font-mono text-xs text-muted">{data ? `${data.approved.conversations.toLocaleString()} conversations` : ''}</span>
            </div>
            <div className="mt-2 h-0.5 bg-[#3a3c42]">
              <div className="h-full bg-gray-100" style={{ width: pct(Math.min(1, approvedReplies / REPLIES_AIM)) }} />
            </div>
          </div>

          {/* criteria */}
          <fieldset className="grid grid-cols-2 gap-x-5 gap-y-2">
            <legend className="mb-2 flex w-full items-center text-xs text-muted">
              <span className="flex-1">Keep conversations with</span>
              {customised && (
                <button onClick={() => setDraft({})} className="text-gray-300 hover:text-white">
                  Reset to defaults
                </button>
              )}
            </legend>
            {FIELDS.map((f) => {
              const placeholder = data ? show(data.defaults[f.key], f.scale) : ''
              return (
                <label key={f.key} className="flex items-center gap-1.5 text-[12.5px] text-gray-300">
                  <span className="shrink-0 text-muted">{f.before}</span>
                  <input
                    inputMode="decimal"
                    value={draft[f.key] ?? ''}
                    placeholder={placeholder}
                    onChange={(e) => setDraft((d) => ({ ...d, [f.key]: e.target.value }))}
                    aria-label={`${f.before} … ${f.after}`}
                    className="h-8 w-14 shrink-0 rounded border border-line bg-page px-2 text-[13px] text-gray-100 placeholder:text-gray-400 focus:border-gray-400 focus:outline-none"
                  />
                  <span className="min-w-0 truncate" title={f.after}>
                    {f.after}
                  </span>
                </label>
              )
            })}
          </fieldset>

          {/* what passes */}
          {!data ? (
            <Loading label="Scoring every conversation…" />
          ) : (
            <>
              <div className="border-t border-[#2f3037] pt-4">
                <div className="flex items-center gap-2">
                  <span className="text-[15px]">
                    <span className="font-mono font-semibold">{data.candidates.length.toLocaleString()}</span> conversations pass · about{' '}
                    <span className="font-mono font-semibold">{data.replies.toLocaleString()}</span> replies
                  </span>
                  {loading && <Spinner className="size-3.5 text-muted" />}
                </div>
                <p className="mt-1.5 text-xs leading-relaxed text-muted">
                  {skippedTotal.toLocaleString()} skipped
                  {skippedTotal > 0 &&
                    ': ' +
                      Object.entries(data.skipped)
                        .filter(([, n]) => n)
                        .sort((a, b) => b[1] - a[1])
                        .map(([k, n]) => `${n.toLocaleString()} ${data.reasons[k] || k}`)
                        .join(' · ')}
                  {rejected > 0 && ` · ${rejected} rejected by you`}
                </p>
              </div>

              <div className="flex flex-wrap items-center gap-2 text-[13px]">
                <span className="text-muted">Approve enough to reach</span>
                <span className="flex overflow-hidden rounded border border-line">
                  {[...AIMS, 'all' as const].map((v) => (
                    <button
                      key={v}
                      onClick={() => setAim(v)}
                      aria-pressed={aim === v}
                      className={cx('h-7 px-2.5 text-[12px]', aim === v ? 'bg-gray-100 font-semibold text-ink' : 'text-gray-300 hover:bg-raised')}
                    >
                      {v === 'all' ? 'all of them' : `${v.toLocaleString()} replies`}
                    </button>
                  ))}
                </span>
                <span className="flex-1" />
                <button
                  onClick={() => {
                    rank.current = new Map()
                    setShuffled((n) => n + 1)
                    setSampleSize(SAMPLE)
                    setOpen(null)
                  }}
                  title="Draw a different random sample (and a different set to approve)"
                  className="flex h-7 items-center gap-1.5 rounded border border-line px-2.5 text-[12px] text-gray-300 hover:bg-raised"
                >
                  <LuShuffle className="size-3.5" /> Shuffle
                </button>
              </div>

              {pick.length === 0 ? (
                <p className="text-[13px] text-muted">
                  {data.candidates.length === 0
                    ? 'Nothing passes these criteria. Loosen one to see more.'
                    : `You already have about ${approvedReplies.toLocaleString()} replies approved. Pick a bigger amount to add more.`}
                </p>
              ) : (
                <div>
                  <div className="mb-1 text-xs text-muted">
                    Random sample · {sample.length} of the {pick.length.toLocaleString()} that would be approved. Reject the bad ones; open any to see it in the feed.
                  </div>
                  <ul className="divide-y divide-[#2f3037]">
                    {sample.map((c) => (
                      <SampleRow
                        key={c.id}
                        c={c}
                        authors={authors}
                        expanded={open === c.id}
                        onToggle={() => setOpen(open === c.id ? null : c.id)}
                        onOpen={() => onOpen(c)}
                        onReject={() => reject(c)}
                        rejecting={rejecting === c.id}
                      />
                    ))}
                  </ul>
                  {pick.length > sample.length && (
                    <button onClick={() => setSampleSize((n) => n + SAMPLE)} className="mt-2 text-xs text-gray-300 hover:text-white">
                      Show {Math.min(SAMPLE, pick.length - sample.length)} more
                    </button>
                  )}
                </div>
              )}
            </>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-[#2f3037] px-6 py-3.5">
          {done ? (
            <span className="flex flex-1 items-center gap-2 text-[13px] text-[#6CCB98]">
              Approved {done.count.toLocaleString()} conversations · about {done.replies.toLocaleString()} replies
              <button
                onClick={undo}
                disabled={undoing}
                className="flex h-7 items-center gap-1.5 rounded border border-line px-2.5 text-[12px] text-gray-200 hover:bg-raised disabled:opacity-60"
              >
                {undoing ? <Spinner className="size-3.5" /> : <LuUndo2 className="size-3.5" />} Undo
              </button>
            </span>
          ) : (
            pick.length > 0 && (
              <span className="flex-1 text-xs text-muted">
                Adds about {pickReplies.toLocaleString()} replies, for about {(approvedReplies + pickReplies).toLocaleString()} in all.
              </span>
            )
          )}
          <button onClick={onClose} className="h-9 rounded-md border border-line px-3.5 text-[13px] text-gray-300 hover:bg-menu-hover">
            Close
          </button>
          <button
            onClick={approve}
            disabled={approving || loading || !pick.length}
            className="flex h-9 items-center gap-2 rounded-md bg-gray-100 px-3.5 text-[13px] font-semibold text-ink hover:bg-white disabled:opacity-40"
          >
            {approving && <Spinner />}
            {approving ? 'Approving…' : `Approve ${pick.length.toLocaleString()} ${pick.length === 1 ? 'conversation' : 'conversations'}`}
          </button>
        </div>
      </div>
    </div>
  )
}

function SampleRow(props: {
  c: Candidate
  authors: Record<string, Author>
  expanded: boolean
  onToggle: () => void
  onOpen: () => void
  onReject: () => void
  rejecting: boolean
}) {
  const { c, authors, expanded } = props
  return (
    <li className="py-2">
      <div className="flex items-center gap-2.5">
        <button onClick={props.onToggle} aria-expanded={expanded} className="flex min-w-0 flex-1 items-center gap-2.5 text-left hover:text-white">
          {expanded ? <LuChevronDown className="size-4 shrink-0 text-muted" /> : <LuChevronRight className="size-4 shrink-0 text-muted" />}
          <span className="flex shrink-0 items-center pl-0.5">
            {c.authors.map((a, i) => (
              <Avatar key={a} who={authors[a]} size={18} className={i ? '-ml-1' : ''} />
            ))}
          </span>
          <span className="min-w-0 flex-1 truncate">
            <span className="text-[13px] font-semibold">
              {fmtDay(c.first_ts)} · {fmtRange(c.first_ts, c.last_ts)}
            </span>
            <span className="ml-2 text-xs text-muted">
              {c.messages} msgs · {c.people} people · ~{c.replies} replies
            </span>
          </span>
        </button>
        <button
          onClick={props.onOpen}
          title="Show it in the feed. Come back with the button at the bottom right."
          aria-label="Open in the feed"
          className="grid size-8 place-items-center rounded-md border border-line text-muted hover:bg-raised hover:text-gray-100"
        >
          <LuExternalLink className="size-3.5" />
        </button>
        <button
          onClick={props.onReject}
          disabled={props.rejecting}
          title="Not good training data: archive it so it isn't approved. Ctrl+Z undoes it."
          className="flex h-8 items-center gap-1.5 rounded-md border border-line px-2.5 text-[12.5px] text-gray-300 hover:bg-raised disabled:opacity-60"
        >
          {props.rejecting ? <Spinner className="size-3.5" /> : <LuArchive className="size-3.5" />} Reject
        </button>
      </div>
      {expanded && <Peek c={c} />}
    </li>
  )
}

// The first lines of a sampled conversation, enough to tell chat from logistics.
function Peek({ c }: { c: Candidate }) {
  const around = useAround(c.first_message_id, 0, 14)
  if (!around) return <Loading label="Loading messages…" className="py-4" />
  if (around.error || !around.around) return <p className="py-2 pl-7 text-xs text-muted">Couldn’t load its messages.</p>
  const lines = around.around.messages.filter((m) => m.ts <= c.last_ts)
  return (
    <ul className="mt-2 ml-7 flex flex-col gap-1.5 rounded-md border border-[#2f3037] bg-page px-3 py-2.5">
      {lines.map((m) => (
        <li key={m.id} className={cx('text-[12.5px] leading-snug', m.excluded ? 'text-muted line-through' : 'text-gray-200')}>
          <span className="font-semibold">{m.author.display_name || m.author.name}</span>
          <span className="ml-1.5 font-mono text-[10.5px] text-muted">{fmtTime(m.ts)}</span>
          <div className="line-clamp-3 break-words">{m.content_text || <i className="text-muted">{m.files ? 'sent a file' : 'said nothing in words'}</i>}</div>
        </li>
      ))}
      {c.messages > lines.length && <li className="text-xs text-muted">…{(c.messages - lines.length).toLocaleString()} more. Open it to read the rest.</li>}
    </ul>
  )
}
