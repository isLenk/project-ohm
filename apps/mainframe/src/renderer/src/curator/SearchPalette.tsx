import { useEffect, useMemo, useRef, useState } from 'react'
import { LuSearch } from 'react-icons/lu'
import { get } from './api'
import type { Author } from './api'
import { color, cx, fmtDay, fmtTime, pct } from './util'

type Hit = { id: string; author_id: string; ts: number; content_text: string; highlights?: number[][]; score?: number }
type Person = { author_id: string; name: string; display_name: string | null; color_index: number; messages: number; share: number; memories: number }
type Result = { messages: Hit[]; similar: Hit[]; people: Person[]; actions: { id: string; label: string }[] }

type Row =
  | { kind: 'message'; hit: Hit }
  | { kind: 'person'; person: Person }
  | { kind: 'action'; id: string; label: string }

type Props = {
  authors: Record<string, Author>
  onClose: () => void
  onMessage: (id: string) => void
  onPerson: (id: string) => void
  onAction: (id: string) => void
}

const OPERATORS = ['@name', '/regex/', 'is:similar', 'is:unattached', 'is:suggested', 'in:approved', 'before:2026-11-05']

export default function SearchPalette({ authors, onClose, onMessage, onPerson, onAction }: Props) {
  const [q, setQ] = useState('')
  const [mode, setMode] = useState<'text' | 'regex'>('text')
  const [res, setRes] = useState<Result | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [sel, setSel] = useState(0)
  const input = useRef<HTMLInputElement>(null)
  const seq = useRef(0)

  useEffect(() => input.current?.focus(), [])

  useEffect(() => {
    const n = ++seq.current
    if (!q.trim()) {
      setRes(null)
      setError(null)
      return
    }
    const t = setTimeout(async () => {
      try {
        const r = await get<Result>(`/search?mode=${mode}&q=${encodeURIComponent(q)}`)
        if (n === seq.current) {
          setRes(r)
          setError(null)
          setSel(0)
        }
      } catch (e: any) {
        if (n === seq.current) setError(e.message)
      }
    }, 120)
    return () => clearTimeout(t)
  }, [q, mode])

  const rows: Row[] = useMemo(() => {
    if (!res) return []
    return [
      ...res.messages.map((hit) => ({ kind: 'message' as const, hit })),
      ...res.similar.map((hit) => ({ kind: 'message' as const, hit })),
      ...res.people.map((person) => ({ kind: 'person' as const, person })),
      ...res.actions.map((a) => ({ kind: 'action' as const, ...a }))
    ]
  }, [res])

  const choose = (r: Row) => {
    onClose()
    if (r.kind === 'message') onMessage(r.hit.id)
    else if (r.kind === 'person') onPerson(r.person.author_id)
    else onAction(r.id)
  }

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') onClose()
    else if (e.key === 'ArrowDown') {
      e.preventDefault()
      setSel((s) => Math.min(rows.length - 1, s + 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setSel((s) => Math.max(0, s - 1))
    } else if (e.key === 'Enter' && rows[sel]) choose(rows[sel])
    else if (e.key === 'Tab') {
      e.preventDefault()
      setMode((m) => (m === 'text' ? 'regex' : 'text'))
    }
  }

  const highlight = (h: Hit) => {
    const spans = h.highlights || []
    if (!spans.length) return h.content_text
    const out: React.ReactNode[] = []
    let at = 0
    spans.forEach(([a, b], i) => {
      if (a < at) return
      out.push(h.content_text.slice(at, a))
      out.push(
        <span key={i} className="font-semibold text-white underline decoration-[#6b6e76] underline-offset-[3px]">
          {h.content_text.slice(a, b)}
        </span>
      )
      at = b
    })
    out.push(h.content_text.slice(at))
    return out
  }

  let index = -1
  const row = (r: Row, body: React.ReactNode, key: string) => {
    index += 1
    const i = index
    return (
      <button
        key={key}
        onMouseMove={() => setSel(i)}
        onClick={() => choose(r)}
        className={cx('flex h-[42px] w-full items-center gap-3 px-4 text-left text-[13.5px]', sel === i ? 'bg-[#2a2b31] text-white shadow-[inset_2px_0_0_#f3f4f6]' : 'text-gray-300')}
      >
        {body}
      </button>
    )
  }
  const group = (title: string) => <div className="px-4 pt-3.5 pb-1.5 text-[11.5px] text-muted">{title}</div>
  const dot = (authorId: string) => <span className="size-2 shrink-0 rounded-full" style={{ background: color(authors[authorId]?.color_index) }} />
  const msgRow = (h: Hit) =>
    row(
      { kind: 'message', hit: h },
      <>
        {dot(h.author_id)}
        <span className="min-w-0 flex-1 truncate">{highlight(h)}</span>
        <span className="font-mono text-[11.5px] text-muted">
          {fmtDay(h.ts)} {fmtTime(h.ts)}
        </span>
      </>,
      `m${h.id}${h.score ?? ''}`
    )

  return (
    <div className="fixed inset-0 z-50 flex justify-center bg-black/50 pt-[10vh]" onMouseDown={onClose}>
      <div
        role="dialog"
        aria-label="Search"
        className="h-fit max-h-[75vh] w-[680px] max-w-[calc(100vw-32px)] overflow-hidden rounded-lg border border-[#3a3b42] bg-[#1d1e22]"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="flex h-14 items-center gap-2.5 border-b border-[#2f3037] px-4">
          <LuSearch className="size-[17px] text-muted" />
          <input
            ref={input}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={onKey}
            placeholder="Search messages, @people, /regex/…"
            aria-label="Search"
            className="min-w-0 flex-1 bg-transparent text-base outline-none placeholder:text-muted"
          />
          <div role="radiogroup" aria-label="Match mode" className="flex gap-0.5">
            {(['text', 'regex'] as const).map((m) => (
              <button
                key={m}
                role="radio"
                aria-checked={mode === m}
                onClick={() => setMode(m)}
                className={cx('h-[26px] rounded px-2.5 text-xs capitalize', mode === m ? 'bg-raised text-gray-100' : 'text-muted')}
              >
                {m}
              </button>
            ))}
          </div>
        </div>
        <div className="flex flex-wrap gap-3.5 border-b border-[#2f3037] px-4 py-2.5 text-xs text-muted">
          {OPERATORS.map((o) => (
            <button key={o} className="font-mono text-gray-300 hover:text-white" onClick={() => setQ((x) => `${x}${x && !x.endsWith(' ') ? ' ' : ''}${o.startsWith('before') ? 'before:' : o === '@name' ? '@' : o === '/regex/' ? '/' : o + ' '}`)}>
              {o}
            </button>
          ))}
        </div>
        <div className="max-h-[52vh] overflow-auto pb-2">
          {error && <div className="px-4 py-4 text-sm text-[#F28B82]">{error}</div>}
          {!error && res && !rows.length && <div className="px-4 py-6 text-sm text-muted">Nothing found.</div>}
          {!res && !error && (
            <div className="px-4 py-6 text-sm leading-relaxed text-muted">
              Type to search every imported message. Press Tab to switch between text and regex.
            </div>
          )}
          {res && res.messages.length > 0 && group('Messages')}
          {res?.messages.map(msgRow)}
          {res && res.similar.length > 0 && group('Similar wording, other people')}
          {res?.similar.map(msgRow)}
          {res && res.people.length > 0 && group('People')}
          {res?.people.map((p) =>
            row(
              { kind: 'person', person: p },
              <>
                <span className="size-2 shrink-0 rounded-full" style={{ background: color(p.color_index) }} />
                <span className="flex-1">{p.display_name || p.name}</span>
                <span className="text-xs text-muted">
                  {p.messages} messages · {pct(p.share)} · {p.memories} memories
                </span>
              </>,
              `p${p.author_id}`
            )
          )}
          {res && res.actions.length > 0 && group('Actions')}
          {res?.actions.map((a) => row({ kind: 'action', id: a.id, label: a.label }, <span className="pl-5">{a.label}</span>, `a${a.id}`))}
        </div>
        <div className="flex h-10 items-center gap-4 border-t border-[#2f3037] px-4 text-xs text-muted">
          <span>↑↓ move</span>
          <span>↵ jump to message</span>
          <span>Tab text / regex</span>
          <span className="flex-1" />
          <span>Esc close</span>
        </div>
      </div>
    </div>
  )
}

