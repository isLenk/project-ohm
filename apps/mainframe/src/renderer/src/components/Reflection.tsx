import { useState } from 'react'
import { LuSparkles } from 'react-icons/lu'
import { Avatar } from '../curator/Avatar'
import { fmtDay, fmtTime, message, nas, personColor, usePoll } from '../lib/ohm'

/* What reflection does to a character, on the Memories page: the jobs that ran, how its traits
   moved, and how it feels about each person. The memories reflection writes (topics, jokes,
   opinions...) are under Learned, where they can be forgotten before Ohm brings them up. */

type Job = {
  id: number
  status: 'pending' | 'running' | 'done' | 'failed'
  messages: number
  created_at: string
  started_at: string | null
  finished_at: string | null
  result: any
  error: string | null
}
type Trait = { trait: string; value: number; floor: number; ceiling: number; updated_at: string }
type Change = { trait: string; value: number; reason: string; created_at: string }
type Bond = {
  user_id: string
  name: string
  affinity: number
  notes: string | null
  updated_at: string
}

const STATUS_COLOR = {
  pending: 'text-muted',
  running: 'text-warn',
  done: 'text-good',
  failed: 'text-bad'
}
const heading = 'mt-8 mb-1 text-[14px] font-semibold'
const when = (t: string) => `${fmtDay(t)} ${fmtTime(t)}`
const pct = (x: number) => `${Math.max(0, Math.min(1, x)) * 100}%`

/** One line for what a job did: its summary if it wrote one, or a count of each thing it changed. */
function summary(result: any) {
  if (!result) return ''
  if (typeof result === 'string') return result
  if (result.summary) return result.summary
  return Object.entries(result)
    .map(([k, v]) => [k, Array.isArray(v) ? v.length : v])
    .filter(([, n]) => typeof n === 'number' && n > 0)
    .map(([k, n]) => `${n} ${k}`)
    .join(', ')
}

const Reflection = ({ slug, people }: { slug: string | undefined; people: any[] }) => (
  <>
    <Jobs slug={slug} />
    <Traits slug={slug} />
    <Bonds slug={slug} people={people} />
  </>
)

const Jobs = ({ slug }: { slug: string | undefined }) => {
  const [tick, setTick] = useState(0)
  const [note, setNote] = useState('')
  const jobs = usePoll<Job[]>(
    () => (slug ? nas.get(`/reflections?character=${slug}&limit=20`) : Promise.resolve([])),
    10000,
    [slug, tick]
  )
  // The NAS turns a second request down with 409 while one is still queued; its reason is shown as is
  const reflect = () =>
    nas
      .post(`/reflections/request?character=${slug}`)
      .then(() => setNote('Queued. It shows up below once it starts.'))
      .catch((e) => setNote(message(e)))
      .finally(() => setTick((t) => t + 1))

  return (
    <>
      <div className="mt-5 flex flex-wrap items-center gap-3">
        <h3 className="text-[14px] font-semibold">Reflections</h3>
        <span className="flex-1" />
        <button className="btn h-8" disabled={!slug} onClick={reflect}>
          <LuSparkles /> Reflect now
        </button>
      </div>
      <p className="mt-1 text-[12.5px] text-muted">
        Runs on its own after a conversation with 40+ new messages has gone quiet for 30 minutes.
      </p>
      {note && <p className="mt-2 text-[12.5px] text-soft">{note}</p>}
      {jobs.error && <p className="mt-2 text-[12.5px] text-bad">{jobs.error}</p>}
      <div className="mt-2">
        {(jobs.data ?? []).map((j) => (
          <div
            key={j.id}
            className="grid grid-cols-[64px_92px_minmax(0,1fr)] items-baseline gap-3 border-b border-line py-2.5 text-[13px]"
          >
            <span
              className={`text-[11px] font-medium uppercase ${STATUS_COLOR[j.status] ?? 'text-muted'}`}
            >
              {j.status}
            </span>
            <span className="font-mono text-[11.5px] text-muted" title={`${j.messages} messages`}>
              {when(j.finished_at ?? j.started_at ?? j.created_at)}
            </span>
            <span className="min-w-0 truncate" title={j.error ?? summary(j.result)}>
              <span className="text-muted">{j.messages} messages · </span>
              {j.error ? (
                <span className="text-bad">{j.error}</span>
              ) : (
                <span className="text-[#d6d7db]">{summary(j.result) || '—'}</span>
              )}
            </span>
          </div>
        ))}
        {jobs.data?.length === 0 && (
          <p className="py-3 text-[13px] text-muted">No reflections yet.</p>
        )}
      </div>
    </>
  )
}

const Traits = ({ slug }: { slug: string | undefined }) => {
  const data = usePoll<{ traits: Trait[]; history: Change[] }>(
    () =>
      slug
        ? nas.get(`/traits?character=${slug}&days=30`)
        : Promise.resolve({ traits: [], history: [] }),
    30000,
    [slug]
  )
  const traits = data.data?.traits ?? []
  const history = [...(data.data?.history ?? [])].sort(
    (a, b) => +new Date(b.created_at) - +new Date(a.created_at)
  )
  // Each change only says where a trait landed, so where it came from is the change before it
  const before = (c: Change, i: number) =>
    history.slice(i + 1).find((h) => h.trait === c.trait)?.value

  return (
    <>
      <h3 className={heading}>Traits</h3>
      <p className="text-[12.5px] text-muted">
        How strongly each part of the personality shows. Reflection nudges them, never past the
        marks.
      </p>
      {data.error && <p className="mt-2 text-[12.5px] text-bad">{data.error}</p>}
      <div className="mt-2">
        {traits.map((t) => (
          <div
            key={t.trait}
            className="grid grid-cols-[140px_minmax(0,1fr)_44px] items-center gap-3 py-1.5 text-[13px]"
          >
            <span className="truncate">{t.trait}</span>
            <span
              className="relative h-1.5 rounded-full bg-[#1e1f22]"
              title={`floor ${t.floor} · ceiling ${t.ceiling}`}
            >
              <span
                className="absolute inset-y-0 left-0 rounded-full bg-clay"
                style={{ width: pct(t.value) }}
              />
              <span className="absolute -inset-y-1 w-px bg-soft" style={{ left: pct(t.floor) }} />
              <span className="absolute -inset-y-1 w-px bg-soft" style={{ left: pct(t.ceiling) }} />
            </span>
            <span className="text-right font-mono text-[11.5px] text-[#c9cbd1]">
              {t.value.toFixed(2)}
            </span>
          </div>
        ))}
        {data.data && traits.length === 0 && (
          <p className="py-3 text-[13px] text-muted">No traits yet.</p>
        )}
      </div>
      {history.length > 0 && (
        <div className="mt-2 flex flex-col gap-1 font-mono text-[11.5px] text-muted">
          {history.slice(0, 6).map((c, i) => {
            const from = before(c, i)
            return (
              <span key={`${c.trait}-${c.created_at}`} className="truncate" title={c.reason}>
                <span className="text-[#c9cbd1]">
                  {c.trait} {from != null && `${from.toFixed(2)} → `}
                  {c.value.toFixed(2)}
                </span>
                : {c.reason}
              </span>
            )
          })}
        </div>
      )}
    </>
  )
}

const Bonds = ({ slug, people }: { slug: string | undefined; people: any[] }) => {
  const bonds = usePoll<Bond[]>(
    () => (slug ? nas.get(`/relationships?character=${slug}`) : Promise.resolve([])),
    30000,
    [slug]
  )
  return (
    <>
      <h3 className={heading}>Relationships</h3>
      <p className="text-[12.5px] text-muted">
        How the character feels about each person, from -1 to 1.
      </p>
      {bonds.error && <p className="mt-2 text-[12.5px] text-bad">{bonds.error}</p>}
      <div className="mt-2">
        {(bonds.data ?? []).map((b) => {
          // The bar grows from the middle: right and green for liking, left and red for not
          const a = Math.max(-1, Math.min(1, b.affinity))
          return (
            <div key={b.user_id} className="border-b border-line py-3">
              <div className="grid grid-cols-[minmax(0,1fr)_160px_44px] items-center gap-3 text-[13.5px]">
                <span className="flex min-w-0 items-center gap-2.5">
                  <Avatar
                    who={
                      people.find((p) => p.user_id === b.user_id) ?? {
                        name: b.name,
                        display_name: null
                      }
                    }
                    tint={personColor(b.user_id)}
                    size={20}
                  />
                  <span className="truncate">{b.name}</span>
                </span>
                <span className="relative h-1.5 rounded-full bg-[#1e1f22]">
                  <span className="absolute -inset-y-1 left-1/2 w-px bg-edge" />
                  <span
                    className={`absolute inset-y-0 rounded-full ${a >= 0 ? 'bg-good' : 'bg-bad'}`}
                    style={
                      a >= 0
                        ? { left: '50%', width: `${a * 50}%` }
                        : { right: '50%', width: `${-a * 50}%` }
                    }
                  />
                </span>
                <span className="text-right font-mono text-[11.5px] text-[#c9cbd1]">
                  {a.toFixed(2)}
                </span>
              </div>
              {b.notes && (
                <p className="mt-1 pl-[30px] text-[12.5px] leading-snug text-soft">{b.notes}</p>
              )}
            </div>
          )
        })}
        {bonds.data?.length === 0 && <p className="py-3 text-[13px] text-muted">Nobody yet.</p>}
      </div>
    </>
  )
}

export default Reflection
