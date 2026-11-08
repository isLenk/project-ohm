import { useState } from 'react'
import { Link } from 'react-router-dom'
import { get } from '../curator/api'
import {
  OHM_COLOR,
  fmtDay,
  fmtTime,
  moduleName,
  nas,
  pct,
  personColor,
  since,
  useCharacters,
  useEngine,
  usePoll,
  type Day
} from '../lib/ohm'

const RANGES = [
  ['24h', 1],
  ['7d', 7],
  ['30d', 30]
] as const
const TAG_COLOR = { update: 'text-warn', error: 'text-bad' }
const SOURCE_COLOR = { brain: 'text-clay', mood: 'text-[#a78bfa]', reflect: 'text-[#5ec8e5]' }
const DAY_COLOR: Record<Day, string> = {
  up: 'bg-[#4e9c74]',
  restarted: 'bg-warn',
  down: 'bg-bad',
  off: 'bg-edge',
  none: 'bg-[#1e1f22]'
}
const TZ = Intl.DateTimeFormat().resolvedOptions().timeZone

const Dashboard = () => {
  const { active } = useCharacters()
  const engine = useEngine()
  const [days, setDays] = useState<number>(7)
  const [tab, setTab] = useState<'people' | 'convs' | 'search'>('people')
  const [person, setPerson] = useState<string | null>(null)
  const [logTab, setLogTab] = useState('all')
  const slug = active?.id

  const ready = (path: string) => () => (slug ? nas.get(path) : Promise.resolve(null))
  const stats = usePoll(ready(`/stats?character=${slug}&days=${days}&tz=${TZ}`), 30000, [
    slug,
    days
  ])
  const people = usePoll(ready(`/people?character=${slug}&days=${days}&limit=30`), 15000, [
    slug,
    days
  ])
  const convs = usePoll(ready(`/conversations?character=${slug}&days=${days}`), 30000, [slug, days])
  const who = person ?? people.data?.[0]?.user_id
  const lines = usePoll(
    () =>
      slug && who
        ? nas.get(`/people/${who}/messages?character=${slug}&limit=12`)
        : Promise.resolve([]),
    5000,
    [slug, who]
  )
  const searches = usePoll(
    () =>
      slug && tab === 'search'
        ? window.api.getSearches
          ? window.api.getSearches(slug, 200)
          : Promise.reject(new Error('restart the app to load it'))
        : Promise.resolve(null),
    5000,
    [slug, tab]
  )
  const health = usePoll(() => window.api.health(days), 10000, [days])
  const logs = usePoll(() => window.api.getLogs(60), 3000)
  // The brain, mood and reflection jobs log to the NAS rather than this app, so the Brain tab reads there
  const brainLogs = usePoll(
    () =>
      logTab === 'brain'
        ? nas.get('/logs?source=brain,mood,reflect&limit=100')
        : Promise.resolve(null),
    5000,
    [logTab]
  )
  const models = usePoll(() => get('/models'), 30000)

  const s = stats.data
  const h = health.data
  const base = models.data?.base_models.find((m) => m.id === active?.model?.base_model_id)
  const adapter = models.data?.adapters.find((a) => a.id === active?.model?.adapter_id)
  // Empty on the Brain tab, which shows the NAS's logs instead
  const system = (logs.data ?? []).filter((l) => logTab === 'all' || l.log_type === logTab)
  const top = s?.top ?? []
  const peak = Math.max(1, ...(s?.hours ?? []).map((x) => x.heard + x.replied))
  const change = s && s.heard_before ? (s.heard - s.heard_before) / s.heard_before : null

  return (
    <div className="flex min-h-full flex-col">
      <header className="app-titlebar flex min-h-11 flex-wrap items-center gap-x-4 gap-y-2 border-b border-line px-6 py-2">
        <h1 className="text-[15px] font-semibold">Dashboard</h1>
        {active && (
          <Link
            to="/characters"
            className="inline-flex h-7 items-center gap-2 rounded-md border border-edge pr-2.5 pl-1.5 text-[12.5px] hover:bg-raised"
          >
            <span className="grid size-[18px] place-items-center rounded border border-accent text-[10px] font-semibold text-clay">
              {active.name[0]}
            </span>
            {active.name}
            <span className="font-mono text-[11px] text-muted">
              · {base?.name ?? 'no model'}
              {adapter && ` + ${adapter.name}`}
            </span>
          </Link>
        )}
        <span className="inline-flex items-center gap-1.5 text-xs text-soft">
          <span
            className={`size-2 rounded-full ${engine?.state === 'running' ? 'bg-good' : 'bg-[#4a4d55]'}`}
          />
          {engine?.state === 'running' ? `Engine up ${since(engine.startedAt)}` : 'Engine stopped'}
        </span>
        <span className="flex-1" />
        <div
          role="tablist"
          aria-label="Range"
          className="flex gap-0.5 rounded-md border border-edge p-0.5"
        >
          {RANGES.map(([label, d]) => (
            <button
              key={label}
              role="tab"
              aria-selected={days === d}
              onClick={() => setDays(d)}
              className={`h-6 cursor-pointer rounded px-2.5 text-xs font-medium ${days === d ? 'bg-[#26272b] text-gray-100' : 'text-muted'}`}
            >
              {label}
            </button>
          ))}
        </div>
      </header>

      <div className="flex flex-1 flex-col gap-4 px-6 pt-4 pb-6">
        <section
          aria-label="Key numbers"
          className="panel grid grid-cols-[repeat(auto-fit,minmax(124px,1fr))] overflow-hidden"
        >
          <Kpi
            label="Messages heard"
            value={s?.heard}
            note={change == null ? '' : `${change >= 0 ? '+' : ''}${Math.round(change * 100)}%`}
            good={change != null && change >= 0}
          />
          <Kpi
            label="Replies sent"
            value={s?.replied}
            note={s?.heard ? `${Math.round((s.replied / s.heard) * 100)}% rate` : ''}
          />
          <Kpi
            label="Reply time"
            value={ms(s?.reply_ms?.median)}
            note={s?.reply_ms?.p95 ? `p95 ${ms(s.reply_ms.p95)}` : ''}
          />
          <Kpi label="People" value={s?.people} note={s ? `${s.new_people} new` : ''} />
          <Kpi
            label="Voice / text"
            value={
              s?.total
                ? `${Math.round((s.voice / s.total) * 100)}/${100 - Math.round((s.voice / s.total) * 100)}`
                : undefined
            }
            note="% of messages"
          />
          <Kpi
            label="Uptime"
            value={pct(h?.engine.uptime)}
            note={engine?.state === 'running' ? `alive ${since(engine.startedAt)}` : 'stopped'}
          />
          <Kpi
            label="Restarts"
            value={h?.engine.restarts}
            note={h ? `${h.engine.crashes} crash${h.engine.crashes === 1 ? '' : 'es'}` : ''}
          />
          <Kpi
            label="Errors"
            value={h?.engine.errors}
            note="logged"
            bad={!!h?.engine.errors}
            last
          />
        </section>
        {stats.error && <p className="text-xs text-muted">Activity: {stats.error}</p>}

        <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,420px),1fr))] gap-4">
          <section aria-labelledby="int" className="panel flex h-75 flex-col overflow-hidden">
            <div className="flex min-h-10 items-center gap-4.5 border-b border-line px-3.5 text-[13px] font-semibold">
              <h2 id="int">Interactions</h2>
              <div role="tablist" aria-label="Group by" className="flex gap-4">
                <button
                  role="tab"
                  aria-selected={tab === 'people'}
                  className="tab text-[12.5px]"
                  onClick={() => setTab('people')}
                >
                  People
                </button>
                <button
                  role="tab"
                  aria-selected={tab === 'convs'}
                  className="tab text-[12.5px]"
                  onClick={() => setTab('convs')}
                >
                  Conversations
                </button>
                <button
                  role="tab"
                  aria-selected={tab === 'search'}
                  className="tab text-[12.5px]"
                  onClick={() => setTab('search')}
                >
                  Web search
                </button>
              </div>
            </div>
            {tab === 'search' ? (
              <Searches list={searches.data} error={searches.error} days={days} />
            ) : tab === 'people' ? (
              <div className="flex min-h-0 flex-1">
                <div
                  role="listbox"
                  aria-label="People"
                  className="w-45 shrink-0 overflow-auto border-r border-line py-1"
                >
                  {(people.data ?? []).map((p) => (
                    <button
                      key={p.user_id}
                      role="option"
                      aria-selected={p.user_id === who}
                      onClick={() => setPerson(p.user_id)}
                      className="flex h-10 w-full cursor-pointer items-center gap-2.5 px-3 text-left text-[13px] font-medium text-[#c9cbd1] hover:bg-[#17181a] aria-selected:bg-raised aria-selected:text-gray-100"
                    >
                      <span
                        className="size-2 shrink-0 rounded-full"
                        style={{ background: personColor(p.user_id) }}
                      />
                      <span className="min-w-0 flex-1 truncate">{p.name}</span>
                      <span className="font-mono text-[11px] text-muted">{since(p.last_seen)}</span>
                    </button>
                  ))}
                  {people.data?.length === 0 && (
                    <p className="px-3 py-2 text-xs text-muted">Nobody yet.</p>
                  )}
                </div>
                <div className="min-w-0 flex-1 overflow-auto px-3.5 py-2">
                  {(lines.data ?? []).map((l) => (
                    <div
                      key={l.id}
                      className="grid grid-cols-[58px_56px_minmax(0,1fr)] items-baseline gap-2.5 py-1 text-[13px] leading-snug"
                    >
                      <span className="text-right font-mono text-[11px] text-muted">
                        {fmtTime(l.created_at)}
                      </span>
                      <span className="flex items-center gap-1.5 truncate text-[12.5px] font-semibold">
                        <span
                          className="size-1.5 shrink-0 rounded-full"
                          style={{
                            background: l.role === 'assistant' ? OHM_COLOR : personColor(l.user_id)
                          }}
                        />
                        {l.role === 'assistant'
                          ? active?.name
                          : people.data?.find((p) => p.user_id === l.user_id)?.name}
                      </span>
                      <span className="text-[#d6d7db]">{l.content}</span>
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <div className="min-h-0 flex-1 overflow-auto">
                {(convs.data ?? []).map((c) => (
                  <div
                    key={`${c.channel_id}-${c.started_at}`}
                    className="grid h-10 grid-cols-[minmax(0,1.3fr)_56px_minmax(0,1.5fr)_40px] items-center gap-3 border-b border-[#1c1d20] px-3.5 text-[13px] text-[#c9cbd1]"
                  >
                    <span className="truncate">
                      <span className="font-semibold text-gray-100">
                        {fmtTime(c.started_at)} – {fmtTime(c.ended_at)}
                      </span>{' '}
                      <span className="text-[11.5px] text-muted">{fmtDay(c.started_at)}</span>
                    </span>
                    <Tag>{c.voice ? 'Voice' : 'Text'}</Tag>
                    <span className="truncate">{c.people.join(', ') || '—'}</span>
                    <span className="text-right font-mono text-xs">{c.messages}</span>
                  </div>
                ))}
                {convs.data?.length === 0 && (
                  <p className="px-3.5 py-3 text-xs text-muted">No conversations in this range.</p>
                )}
              </div>
            )}
          </section>

          <section aria-labelledby="act" className="panel flex h-75 flex-col overflow-hidden">
            <div className="flex min-h-10 items-center gap-3 border-b border-line px-3.5 text-[13px] font-semibold">
              <h2 id="act" className="flex-1">
                Activity by hour
              </h2>
              <Legend color="bg-[#5a5d65]">Heard</Legend>
              <Legend color="bg-clay">Replied</Legend>
            </div>
            <div className="flex min-h-0 flex-1 gap-5 px-3.5 py-3">
              <figure className="flex min-w-0 flex-1 flex-col">
                <div
                  role="img"
                  aria-label="Messages heard and replies sent per hour of the day"
                  className="flex flex-1 items-end gap-[3px] border-b border-edge"
                >
                  {(s?.hours ?? []).map((x) => (
                    <div
                      key={x.hour}
                      title={`${x.hour}:00 · ${x.heard} heard, ${x.replied} replied`}
                      className="flex h-full flex-1 flex-col justify-end gap-px"
                    >
                      <div
                        className="rounded-t-sm bg-[#5a5d65]"
                        style={{ height: `${(x.heard / peak) * 100}%` }}
                      />
                      <div className="bg-clay" style={{ height: `${(x.replied / peak) * 100}%` }} />
                    </div>
                  ))}
                </div>
                <div className="mt-1.5 flex justify-between font-mono text-[10.5px] text-muted">
                  <span>12a</span>
                  <span>6a</span>
                  <span>12p</span>
                  <span>6p</span>
                  <span>11p</span>
                </div>
              </figure>
              <div className="flex w-[170px] shrink-0 flex-col">
                <div className="mb-1.5 text-[11.5px] text-muted">Talks with most</div>
                {top.map((t) => (
                  <div key={t.user_id} className="flex h-9.5 flex-col justify-center gap-1">
                    <span className="flex items-center gap-1.5 text-xs">
                      <span
                        className="size-1.5 rounded-full"
                        style={{ background: personColor(t.user_id) }}
                      />
                      <span className="min-w-0 flex-1 truncate">{t.name}</span>
                      <span className="font-mono text-[11px] text-soft">{t.messages}</span>
                    </span>
                    <span className="h-1 rounded-full bg-[#1e1f22]">
                      <span
                        className="block h-1 rounded-full bg-[#8a8d95]"
                        style={{ width: `${(t.messages / top[0].messages) * 100}%` }}
                      />
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </section>
        </div>

        <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,420px),1fr))] gap-4">
          <section aria-labelledby="mods" className="panel">
            <div className="flex min-h-10 items-center gap-3 border-b border-line px-3.5 text-[13px] font-semibold">
              <h2 id="mods">Modules</h2>
              <span className="text-xs font-normal text-muted">last 30 days</span>
              <span className="flex-1" />
              <Link to="/modules" className="text-xs font-medium text-soft hover:text-gray-100">
                Manage
              </Link>
            </div>
            <div className="overflow-x-auto px-3.5 pb-1.5">
              <table className="w-full min-w-[480px] border-collapse text-[12.5px]">
                <thead>
                  <tr className="text-left text-[11px] text-muted">
                    <th scope="col" className="h-7.5 font-medium">
                      Module
                    </th>
                    <th scope="col" className="w-[34%] font-medium">
                      Uptime
                    </th>
                    <th scope="col" className="text-right font-medium">
                      Alive
                    </th>
                    <th scope="col" className="text-right font-medium" title="Restarts">
                      Rst
                    </th>
                    <th scope="col" className="text-right font-medium" title="Errors">
                      Err
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <ModuleRow
                    name="Python side"
                    stats={h?.engine}
                    ok={engine?.state === 'running'}
                    alive={engine?.state === 'running' ? engine.startedAt : null}
                  />
                  {Object.entries(h?.modules ?? {}).map(([name, m]) => (
                    <ModuleRow
                      key={name}
                      name={moduleName(name)}
                      stats={m}
                      ok={m.since != null}
                      alive={m.since}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section aria-labelledby="log" className="panel flex flex-col">
            <div className="flex min-h-10 items-center border-b border-line px-3.5 text-[13px] font-semibold">
              <h2 id="log" className="flex-1">
                System log
              </h2>
              <div role="tablist" aria-label="Log type" className="flex gap-3.5">
                {['all', 'update', 'error', 'brain'].map((t) => (
                  <button
                    key={t}
                    role="tab"
                    aria-selected={logTab === t}
                    className="tab text-[12.5px] capitalize"
                    onClick={() => setLogTab(t)}
                  >
                    {t}
                  </button>
                ))}
              </div>
            </div>
            <ul className="max-h-[210px] flex-1 overflow-auto py-1.5 font-mono text-[11.5px]">
              {logTab === 'brain' && <BrainLog list={brainLogs.data} error={brainLogs.error} />}
              {system.map((l) => (
                <li
                  key={l._id}
                  className="grid grid-cols-[52px_54px_minmax(0,1fr)] gap-2.5 px-3.5 py-1"
                >
                  <span className="text-muted">{fmtTime(l.timestamp)}</span>
                  <span
                    className={`text-[10.5px] uppercase ${TAG_COLOR[l.log_type] ?? 'text-muted'}`}
                  >
                    {l.log_type ?? 'info'}
                  </span>
                  <span className="break-all text-[#c9cbd1]">{l.message}</span>
                </li>
              ))}
              {logTab !== 'brain' && system.length === 0 && <li className="px-3.5 py-1 text-muted">Nothing logged.</li>}
            </ul>
          </section>
        </div>
      </div>
    </div>
  )
}

type NasLog = {
  id: number
  level: string
  source: string
  message: string
  data: Record<string, any> | null
  created_at: string
}

/** role · backend · ms · tokens · $, from what the brain package logs with each call. */
function brainDetail(d: Record<string, any> | null) {
  if (!d) return ''
  const tokens = d.tokens ?? (d.prompt_tokens != null || d.output_tokens != null
    ? (d.prompt_tokens ?? 0) + (d.output_tokens ?? 0)
    : null)
  const usd = d.usd ?? d.cost_usd
  return [
    d.role,
    d.backend,
    d.ms != null && ms(d.ms),
    tokens != null && `${tokens} tok`,
    usd != null && `$${Number(usd).toFixed(4)}`
  ]
    .filter(Boolean)
    .join(' · ')
}

/** Rows for the Brain tab, laid out like the system log's. A brain row opens to show what came back. */
const BrainLog = ({ list, error }: { list: NasLog[] | null; error: string | null }) => {
  const [open, setOpen] = useState<number | null>(null)
  if (error) return <li className="px-3.5 py-1 text-muted">{error}</li>
  if (list?.length === 0) return <li className="px-3.5 py-1 text-muted">Nothing logged.</li>
  return (
    <>
      {(list ?? []).map((l) => (
        <BrainRow
          key={l.id}
          l={l}
          open={open === l.id}
          onToggle={() => setOpen(open === l.id ? null : l.id)}
        />
      ))}
    </>
  )
}

const BrainRow = ({ l, open, onToggle }: { l: NasLog; open: boolean; onToggle: () => void }) => {
  const detail = l.source === 'brain' ? brainDetail(l.data) : ''
  const preview = l.data?.preview
  return (
    <li
      title={preview}
      onClick={() => preview && onToggle()}
      className={`grid grid-cols-[52px_54px_minmax(0,1fr)] gap-2.5 px-3.5 py-1 ${preview ? 'cursor-pointer hover:bg-[#17181a]' : ''}`}
    >
      <span className="text-muted">{fmtTime(l.created_at)}</span>
      <span className={`text-[10.5px] uppercase ${SOURCE_COLOR[l.source] ?? 'text-muted'}`}>
        {l.source}
      </span>
      <span className="min-w-0">
        <span className={`break-all ${l.level === 'error' ? 'text-bad' : 'text-[#c9cbd1]'}`}>
          {l.message}
        </span>
        {detail && <span className="block text-[10.5px] text-muted">{detail}</span>}
        {open && <span className="mt-1 block whitespace-pre-wrap text-soft">{preview}</span>}
      </span>
    </li>
  )
}

type Search = {
  _id: string
  said: string
  query: string
  results: { title: string; body: string; href: string }[]
  error: string | null
  ms: number | null
  at: string
}

/** Every web search Ohm made in the range: what was said, what he searched, and what came back. */
const Searches = ({ list, error, days }: { list: Search[] | null; error: string | null; days: number }) => {
  const [open, setOpen] = useState<string | null>(null)
  const from = Date.now() - days * 86400000
  const shown = (list ?? [])
    .filter((s) => new Date(s.at).getTime() >= from)
    .map((s) => ({ ...s, results: s.results ?? [] }))
  return (
    <div className="min-h-0 flex-1 overflow-auto">
      {shown.map((s) => (
        <div key={s._id} className="border-b border-[#1c1d20]">
          <button
            aria-expanded={open === s._id}
            onClick={() => setOpen(open === s._id ? null : s._id)}
            title={s.said}
            className="grid min-h-10 w-full cursor-pointer grid-cols-[58px_minmax(0,1fr)_auto_52px] items-center gap-3 px-3.5 text-left text-[13px] text-[#c9cbd1] hover:bg-[#17181a]"
          >
            <span className="text-right font-mono text-[11px] text-muted">
              {fmtTime(s.at)}
              <span className="block text-[10px]">{fmtDay(s.at)}</span>
            </span>
            <span className="truncate font-medium text-gray-100">{s.query}</span>
            {s.error ? (
              <span className="text-[11.5px] text-bad">failed</span>
            ) : (
              <Tag>
                {s.results.length} result{s.results.length === 1 ? '' : 's'}
              </Tag>
            )}
            <span className="text-right font-mono text-[11px] text-muted">{ms(s.ms)}</span>
          </button>
          {open === s._id && (
            <div className="flex flex-col gap-2 px-3.5 pb-3 pl-[86px] text-[12.5px]">
              <p className="text-muted">
                Asked: <span className="text-[#d6d7db]">{s.said}</span>
              </p>
              {s.error && <p className="font-mono text-[11.5px] text-bad">{s.error}</p>}
              {s.results.map((r, i) => (
                <div key={i} className="leading-snug">
                  <a
                    href={r.href}
                    target="_blank"
                    rel="noreferrer"
                    className="font-medium text-gray-100 hover:underline"
                  >
                    {r.title || r.href}
                  </a>
                  <p className="line-clamp-2 text-[#b9bbc1]">{r.body}</p>
                </div>
              ))}
              {!s.error && s.results.length === 0 && <p className="text-muted">Nothing came back.</p>}
            </div>
          )}
        </div>
      ))}
      {error && <p className="px-3.5 py-3 text-xs text-muted">Web search: {error}</p>}
      {list && shown.length === 0 && (
        <p className="px-3.5 py-3 text-xs text-muted">No web searches in this range.</p>
      )}
    </div>
  )
}

const ms = (x: number | null | undefined) =>
  x == null ? '—' : x < 1000 ? `${Math.round(x)} ms` : `${(x / 1000).toFixed(1)} s`

const Kpi = ({ label, value, note, good = false, bad = false, last = false }) => (
  <div
    className={`flex min-w-0 flex-col gap-1.5 px-3.5 py-3 ${last ? '' : 'border-r border-line'}`}
  >
    <span className="truncate text-[11.5px] text-muted">{label}</span>
    <span className="text-[19px] leading-none font-semibold whitespace-nowrap tabular-nums">
      {value ?? '—'}
    </span>
    <span
      className={`truncate font-mono text-[11px] ${good ? 'text-good' : bad ? 'text-bad' : 'text-muted'}`}
    >
      {note}
    </span>
  </div>
)

const Tag = ({ children }) => (
  <span className="justify-self-start rounded border border-edge px-1.5 py-0.5 text-[10.5px] font-medium text-soft">
    {children}
  </span>
)

const Legend = ({ color, children }) => (
  <span className="inline-flex items-center gap-1.5 text-[11.5px] font-normal text-soft">
    <span className={`size-2 rounded-sm ${color}`} />
    {children}
  </span>
)

const ModuleRow = ({ name, stats, ok, alive }) => (
  <tr className={stats?.uptime == null ? 'text-muted' : ''}>
    <th scope="row" className="h-8.5 border-b border-[#1c1d20] text-left font-medium">
      <span className="inline-flex items-center gap-2">
        <span className={`size-2 rounded-full ${ok ? 'bg-good' : 'bg-[#4a4d55]'}`} />
        {name}
      </span>
    </th>
    <td className="border-b border-[#1c1d20] pr-3.5">
      <span className="flex items-center gap-2">
        <span className="flex h-3 flex-1 gap-px" title="One bar per day">
          {(stats?.days ?? []).map((d: Day, i: number) => (
            <span key={i} className={`flex-1 rounded-[1px] ${DAY_COLOR[d]}`} />
          ))}
        </span>
        <span className="w-10 text-right font-mono text-[11px] text-[#c9cbd1]">
          {pct(stats?.uptime)}
        </span>
      </span>
    </td>
    <td className="border-b border-[#1c1d20] text-right font-mono text-[11.5px]">
      {alive ? since(alive) : '—'}
    </td>
    <td className="border-b border-[#1c1d20] text-right font-mono text-[11.5px]">
      {stats?.restarts ?? '—'}
    </td>
    <td
      className={`border-b border-[#1c1d20] text-right font-mono text-[11.5px] ${stats?.errors ? 'text-bad' : ''}`}
    >
      {stats?.errors ?? '—'}
    </td>
  </tr>
)

export default Dashboard
