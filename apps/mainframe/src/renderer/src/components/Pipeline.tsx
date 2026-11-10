import { useEffect, useState } from 'react'
import { fmtTime, useEngine, usePoll } from '../lib/ohm'

// One turn through the engine's stages (from main/turns.js). Hover a stage for its logs, click to pin.

type StageStatus = 'running' | 'done' | 'skipped' | 'failed' | 'waiting'
type Stage = {
  status: StageStatus
  started_at: number
  ended_at: number | null
  note: string
  logs: { at: number; text: string }[]
}
type Turn = {
  id: string
  kind: 'voice' | 'idle' | 'text'
  character: string
  speakers: string[]
  said: string
  status: 'hearing' | 'running' | 'done' | 'cut' | 'failed' | 'lost'
  started_at: number
  ended_at: number | null
  end_note?: string
  stages: Record<string, Stage>
  marks: Record<string, number>
  reply: string
}

type Node = { id: string; label: string; lane: number; col: number; kinds: Turn['kind'][] }
const ALL: Turn['kind'][] = ['voice', 'idle', 'text']
const SPOKEN: Turn['kind'][] = ['voice', 'idle']
const NODES: Node[] = [
  { id: 'discord', label: 'Discord', lane: 0, col: 0, kinds: ['voice', 'text'] },
  { id: 'stt', label: 'STT', lane: 0, col: 1, kinds: ['voice'] },
  { id: 'queue', label: 'Text queue', lane: 0, col: 2, kinds: ['voice'] },
  { id: 'turn', label: 'Turn', lane: 0, col: 3, kinds: SPOKEN },
  { id: 'idle', label: 'Idle worker', lane: 0, col: 5, kinds: ['idle'] },
  { id: 'memory', label: 'Memory recall', lane: 1, col: 0, kinds: ALL },
  { id: 'checks', label: 'Pre-checks', lane: 1, col: 1, kinds: ALL },
  { id: 'search', label: 'Web search', lane: 1, col: 2, kinds: ALL },
  { id: 'screen', label: 'Screen look', lane: 1, col: 3, kinds: ALL },
  { id: 'prompt', label: 'Prompt', lane: 1, col: 4, kinds: ALL },
  { id: 'llm', label: 'LLM', lane: 1, col: 5, kinds: ALL },
  { id: 'tts', label: 'TTS', lane: 2, col: 3, kinds: SPOKEN },
  { id: 'playback', label: 'Playback', lane: 2, col: 4, kinds: SPOKEN },
  { id: 'commit', label: 'Commit', lane: 2, col: 5, kinds: ALL }
]
const LANES = ['Hear', 'Think', 'Speak']
type Edge = { from: string; to: string; shape: 'right' | 'down' | 'left' }
const EDGES: Edge[] = [
  { from: 'discord', to: 'stt', shape: 'right' },
  { from: 'stt', to: 'queue', shape: 'right' },
  { from: 'queue', to: 'turn', shape: 'right' },
  { from: 'idle', to: 'turn', shape: 'left' },
  { from: 'turn', to: 'memory', shape: 'down' },
  { from: 'memory', to: 'checks', shape: 'right' },
  { from: 'checks', to: 'search', shape: 'right' },
  { from: 'search', to: 'screen', shape: 'right' },
  { from: 'screen', to: 'prompt', shape: 'right' },
  { from: 'prompt', to: 'llm', shape: 'right' },
  { from: 'llm', to: 'tts', shape: 'down' },
  { from: 'tts', to: 'playback', shape: 'right' },
  { from: 'playback', to: 'commit', shape: 'right' }
]
const MARKS = [
  'transcript',
  'bot_got_text',
  'pre_checks',
  'llm_stream_open',
  'llm_first_token',
  'filler',
  'first_tts_audio',
  'playback'
]

const W = 144
const H = 64
const COL = 180
const ROW = 160
const X0 = 32
const Y0 = 64
const HOST_W = X0 + 5 * COL + W + X0
const HOST_H = Y0 + 2 * ROW + H + 72
const POP_W = 440

const GOOD = '#4e9c74'
const ACCENT = '#c4664a'
const BAD = '#f08a8a'
const DIM = '#3a3c42'

const TURN_DOT: Record<Turn['status'], string> = {
  hearing: '#a0a3ab',
  running: ACCENT,
  done: GOOD,
  cut: '#e0b25c',
  failed: BAD,
  lost: BAD
}
const TURN_WORD: Record<Turn['status'], string> = {
  hearing: 'listening',
  running: 'replying',
  done: 'done',
  cut: 'cut off',
  failed: 'failed',
  lost: 'lost'
}

const at = (n: Node) => ({ x: X0 + n.col * COL, y: Y0 + n.lane * ROW })
const node = (id: string) => NODES.find((n) => n.id === id)!

const ms = (x: number) => (x < 1000 ? `${Math.round(x)} ms` : `${(x / 1000).toFixed(1)} s`)

// 'na': not used for this kind of turn
function shown(turn: Turn | null, n: Node): StageStatus | 'na' {
  if (!turn) return 'waiting'
  const s = turn.stages[n.id]
  if (s) return s.status
  return n.kinds.includes(turn.kind) ? 'waiting' : 'na'
}

const Pipeline = () => {
  const engine = useEngine()
  const { data: turns, error } = usePoll(() => window.api.getTurns(40), 1000)
  const [follow, setFollow] = useState(true)
  const [picked, setPicked] = useState<string | null>(null)
  const [hover, setHover] = useState<string | null>(null)
  const [pinned, setPinned] = useState<string | null>(null)
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 250)
    return () => clearInterval(t)
  }, [])

  const list = (turns ?? []) as Turn[]
  const turn = (follow ? list[0] : list.find((t) => t.id === picked)) ?? list[0] ?? null
  const open = pinned ?? hover
  const live = turn && (turn.status === 'running' || turn.status === 'hearing')
  const elapsed = turn ? (turn.ended_at ?? now) - turn.started_at : 0

  return (
    <section aria-labelledby="pipe" className="panel flex min-h-0 flex-1 flex-col overflow-hidden">
      <div className="flex min-h-10 flex-wrap items-center gap-x-4.5 gap-y-1 border-b border-line px-3.5 text-[13px] font-semibold">
        <h2 id="pipe">Pipeline</h2>
        {turn && (
          <span className="font-mono text-[11.5px] font-normal text-soft">
            #{turn.id.split('-')[1]} · {turn.kind} · {turn.speakers.join(', ') || 'nobody'} ·{' '}
            {ms(elapsed)}
            {live ? ' so far' : ''}
            {turn.end_note ? ` · ${turn.end_note}` : ''}
          </span>
        )}
        <span className="flex-1" />
        <ul aria-label="Legend" className="flex gap-3.5 text-[11.5px] font-normal text-soft">
          <Key color={GOOD}>done</Key>
          <Key color={ACCENT}>running</Key>
          <Key color={DIM}>waiting</Key>
          <Key dashed>skipped</Key>
        </ul>
      </div>

      <div className="flex min-h-0 flex-1">
        <div
          role="listbox"
          aria-label="Turns"
          className="flex w-45 shrink-0 flex-col overflow-auto border-r border-line py-1"
        >
          {list.map((t) => (
            <TurnRow
              key={t.id}
              t={t}
              selected={t.id === turn?.id}
              onPick={() => {
                setFollow(false)
                setPicked(t.id)
              }}
            />
          ))}
          {list.length === 0 && <p className="px-3 py-2 text-xs text-muted">No replies yet.</p>}
          <span className="flex-1" />
          <label className="flex items-center gap-2 border-t border-line px-3 py-2.5 text-xs text-soft">
            <input
              type="checkbox"
              checked={follow}
              onChange={(e) => setFollow(e.target.checked)}
              className="m-0 accent-accent"
            />
            Follow the latest
          </label>
        </div>

        <div className="relative min-w-0 flex-1 overflow-auto">
          {error && <p className="px-4 py-3 text-xs text-muted">{error}</p>}
          {!turn && !error && (
            <p className="px-4 py-3 text-[13px] text-soft">
              {engine?.state === 'running'
                ? 'Ohm is listening. The first reply will show up here as it happens.'
                : 'Start the engine to watch replies move through the pipeline.'}
            </p>
          )}
          {turn && (
            <Graph
              turn={turn}
              now={now}
              open={open}
              pinned={pinned}
              onHover={setHover}
              onPin={(id) => setPinned(pinned === id ? null : id)}
            />
          )}
        </div>
      </div>
    </section>
  )
}

const Key = ({
  color,
  dashed,
  children
}: {
  color?: string
  dashed?: boolean
  children: string
}) => (
  <li className="inline-flex items-center gap-1.5">
    <span
      className="size-2 rounded-full"
      style={dashed ? { border: '1px dashed #5a5d66' } : { background: color }}
    />
    {children}
  </li>
)

const TurnRow = ({ t, selected, onPick }: { t: Turn; selected: boolean; onPick: () => void }) => {
  const live = t.status === 'running' || t.status === 'hearing'
  const who = t.speakers.join(', ') || (t.kind === 'idle' ? 'Idle worker' : '—')
  const said = t.said.split('\n')[0]?.replace(/^[^:]{1,40}: /, '')
  const line =
    t.status === 'done' || t.status === 'running' || t.status === 'hearing'
      ? said || (t.kind === 'idle' ? 'spoke unprompted' : TURN_WORD[t.status])
      : `${TURN_WORD[t.status]}${t.end_note ? ` · ${t.end_note}` : ''}`
  return (
    <button
      role="option"
      aria-selected={selected}
      onClick={onPick}
      className="flex h-13 w-full shrink-0 cursor-pointer items-center gap-2.5 px-3 text-left text-[12.5px] font-medium text-[#c9cbd1] hover:bg-[#17181a] aria-selected:bg-raised aria-selected:text-gray-100"
    >
      <span
        className={`size-2 shrink-0 rounded-full ${t.status === 'hearing' ? 'animate-pulse' : ''}`}
        style={{ background: TURN_DOT[t.status] }}
      />
      <span className="flex min-w-0 flex-1 flex-col gap-px">
        <span className="flex justify-between gap-1.5">
          <span className="truncate">{who}</span>
          <span className={`font-mono text-[11px] ${live ? 'text-clay' : 'text-muted'}`}>
            {live ? 'now' : fmtTime(t.ended_at ?? t.started_at)}
          </span>
        </span>
        <span className="truncate text-xs font-normal text-soft">{line}</span>
      </span>
    </button>
  )
}

const Graph = ({
  turn,
  now,
  open,
  pinned,
  onHover,
  onPin
}: {
  turn: Turn
  now: number
  open: string | null
  pinned: string | null
  onHover: (id: string | null) => void
  onPin: (id: string) => void
}) => {
  const status = (id: string) => shown(turn, node(id))
  // Lit once its target stage is reached
  const edgeColor = (e: Edge) => {
    const s = status(e.to)
    if (e.from === 'idle') return turn.kind === 'idle' ? GOOD : DIM
    if (s === 'running') return ACCENT
    if (s === 'failed') return BAD
    if (s === 'done' || s === 'skipped') return GOOD
    return DIM
  }
  return (
    <div className="relative" style={{ width: HOST_W, height: HOST_H }}>
      {LANES.map((name, i) => (
        <div
          key={name}
          className="absolute text-[11px] font-semibold tracking-[0.08em] text-muted uppercase"
          style={{ left: X0, top: Y0 + i * ROW - 26 }}
        >
          {name}
        </div>
      ))}

      <svg
        aria-hidden="true"
        width={HOST_W}
        height={HOST_H}
        className="pointer-events-none absolute inset-0 fill-none"
        style={{ strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round' }}
      >
        <defs>
          {[GOOD, ACCENT, BAD, DIM].map((c) => (
            <marker
              key={c}
              id={`head-${c.slice(1)}`}
              orient="auto"
              markerWidth="5"
              markerHeight="5"
              refX="2"
              refY="2"
              overflow="visible"
            >
              <path d="M0 0 L4 2 L0 4 Z" fill={c} stroke="none" />
            </marker>
          ))}
        </defs>
        {EDGES.map((e) => {
          const c = edgeColor(e)
          const moving = c === ACCENT
          const dashed = e.from === 'idle' && c === DIM
          return (
            <path
              key={`${e.from}-${e.to}`}
              d={edgePath(e)}
              stroke={c}
              markerEnd={`url(#head-${c.slice(1)})`}
              className={moving ? 'flow-dash' : ''}
              strokeDasharray={dashed ? '6 6' : undefined}
            />
          )
        })}
      </svg>

      {NODES.map((n) => {
        const s = status(n.id)
        const stage = turn.stages[n.id]
        const { x, y } = at(n)
        const dur = stage ? (stage.ended_at ?? now) - stage.started_at : 0
        const second =
          stage?.note ||
          (s === 'running'
            ? ms(dur)
            : s === 'na'
              ? 'not this kind of turn'
              : s === 'waiting'
                ? ''
                : s)
        return (
          <button
            key={n.id}
            type="button"
            aria-pressed={pinned === n.id}
            aria-label={`${n.label}: ${s === 'na' ? 'not used' : s}${stage?.note ? `, ${stage.note}` : ''}`}
            onMouseEnter={() => onHover(n.id)}
            onMouseLeave={() => onHover(null)}
            onFocus={() => onHover(n.id)}
            onBlur={() => onHover(null)}
            onClick={() => onPin(n.id)}
            className={`absolute flex cursor-pointer items-center gap-2 rounded-lg border px-2.5 text-left text-[12.5px] leading-[1.3] ${CARD[s]} ${s === 'running' ? 'stage-live' : ''}`}
            style={{ left: x, top: y, width: W, height: H }}
          >
            <span className={`size-2 shrink-0 rounded-full ${DOT[s]}`} />
            <span className="min-w-0">
              <b className="block truncate font-semibold">{n.label}</b>
              <span
                className={`block truncate font-mono text-[11px] ${s === 'running' ? 'text-clay' : s === 'done' ? 'text-soft' : ''}`}
              >
                {second}
              </span>
            </span>
          </button>
        )
      })}

      <div
        className="absolute font-mono text-[11px] text-muted"
        style={{ left: X0, top: Y0 + 2 * ROW + H + 24 }}
      >
        {Object.keys(turn.marks).length
          ? 'marks  ' +
            MARKS.filter((m) => m in turn.marks)
              .map((m) => `${m} +${turn.marks[m]}`)
              .join(' · ')
          : 'marks come in once the reply starts playing'}
      </div>

      {open && <Popover turn={turn} n={node(open)} now={now} pinned={pinned === open} />}
    </div>
  )
}

// Tips stop at the card's edge
function edgePath(e: Edge) {
  const a = at(node(e.from))
  const b = at(node(e.to))
  if (e.shape === 'right') return `M ${a.x + W} ${a.y + H / 2} L ${b.x - 1} ${b.y + H / 2}`
  if (e.shape === 'left') return `M ${a.x} ${a.y + H / 2} L ${b.x + W + 1} ${b.y + H / 2}`
  const mid = a.y + H + (ROW - H) / 2
  return `M ${a.x + W / 2} ${a.y + H} L ${a.x + W / 2} ${mid} L ${b.x + W / 2} ${mid} L ${b.x + W / 2} ${b.y - 1}`
}

const CARD: Record<StageStatus | 'na', string> = {
  done: 'border-edge bg-panel text-gray-100',
  running: 'border-accent bg-[#1d1614] text-gray-100',
  failed: 'border-bad bg-[#1d1414] text-gray-100',
  skipped: 'border-dashed border-[#3a3c42] bg-page text-muted',
  waiting: 'border-line bg-page text-muted',
  na: 'border-line bg-page text-[#55585f]'
}
const DOT: Record<StageStatus | 'na', string> = {
  done: 'bg-[#4e9c74]',
  running: 'bg-accent',
  failed: 'bg-bad',
  skipped: 'border border-dashed border-[#5a5d66]',
  waiting: 'bg-[#3a3c42]',
  na: 'bg-[#26272b]'
}

const Popover = ({
  turn,
  n,
  now,
  pinned
}: {
  turn: Turn
  n: Node
  now: number
  pinned: boolean
}) => {
  const s = turn.stages[n.id]
  const st = shown(turn, n)
  const { x, y } = at(n)
  const left = Math.min(x, HOST_W - 16 - POP_W)
  const below = n.lane < 2
  const dur = s ? (s.ended_at ?? now) - s.started_at : null
  const rel = (t: number) => `+${ms(Math.max(0, t - turn.started_at))}`
  return (
    <div
      role="tooltip"
      className="absolute z-10 flex flex-col rounded-lg border border-edge bg-panel shadow-[0_12px_32px_rgba(0,0,0,.45)]"
      style={{
        left,
        top: below ? y + H + 12 : y - 12,
        width: POP_W,
        transform: below ? undefined : 'translateY(-100%)'
      }}
    >
      <div className="flex items-center gap-2.5 px-3.5 pt-3 pb-2">
        <span className={`size-2 rounded-full ${DOT[st]}`} />
        <span className="text-[13px] font-semibold">{n.label}</span>
        <span className={`font-mono text-[11.5px] ${st === 'running' ? 'text-clay' : 'text-soft'}`}>
          {st === 'na' ? 'not used for this kind of turn' : st}
          {dur != null && st !== 'waiting' ? ` · ${ms(dur)}` : ''}
        </span>
        <span className="flex-1" />
        {s?.note && <span className="truncate font-mono text-[11px] text-muted">{s.note}</span>}
      </div>
      <div className="flex max-h-60 flex-col overflow-auto border-t border-line px-3.5 py-2">
        {(s?.logs ?? []).map((l, i) => (
          <div
            key={i}
            className="grid grid-cols-[72px_minmax(0,1fr)] gap-3 py-1 font-mono text-[11.5px] leading-4 text-[#d4d6db]"
          >
            <span className="text-muted">{rel(l.at)}</span>
            <span className="break-words whitespace-pre-wrap">{l.text}</span>
          </div>
        ))}
        {n.id === 'llm' && turn.reply && (
          <div className="grid grid-cols-[72px_minmax(0,1fr)] gap-3 py-1 font-mono text-[11.5px] leading-4">
            <span className="text-muted">reply</span>
            <span className="break-words whitespace-pre-wrap text-gray-100">
              {turn.reply}
              {st === 'running' ? '▍' : ''}
            </span>
          </div>
        )}
        {!s?.logs?.length && !(n.id === 'llm' && turn.reply) && (
          <p className="py-1 font-mono text-[11.5px] text-muted">
            {st === 'waiting' ? "Hasn't got here yet." : 'Nothing logged for this stage.'}
          </p>
        )}
      </div>
      <div className="border-t border-line px-3.5 py-2 text-[11.5px] text-muted">
        {pinned ? 'Pinned · click the stage again to let go' : 'Click the stage to keep this open'}
      </div>
    </div>
  )
}

export default Pipeline
