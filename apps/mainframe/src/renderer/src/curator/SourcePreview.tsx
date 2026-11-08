import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { LuExternalLink, LuPaperclip, LuX } from 'react-icons/lu'
import { get } from './api'
import { Avatar, BotTag } from './Avatar'
import { cx, fmtTime, fmtWhen } from './util'
import { Loading, Spinner } from '../components/Loader'

// Where a memory or a term came from: the side panel with the messages around a source, and the
// "open" link that fills it (hovering it shows a few lines).

export type Source = { id: string; ts: number; content_text: string }

export type Around = {
  message_id: string
  messages: {
    id: string
    ts: number
    author_id: string
    content_text: string
    excluded: boolean
    files: number
    author: { name: string; display_name: string | null; avatar_url: string | null; color_index: number; is_bot: boolean }
  }[]
}

// ── source context ───────────────────────────────────────────────────────────
const aroundCache = new Map<string, Promise<Around>>()

function fetchAround(id: string, before: number, after: number): Promise<Around> {
  const key = `${id}:${before}:${after}`
  let p = aroundCache.get(key)
  if (!p) {
    p = get<Around>(`/messages/${id}/around?before=${before}&after=${after}`)
    p.catch(() => aroundCache.delete(key))
    aroundCache.set(key, p)
  }
  return p
}

export function useAround(id: string | null, before: number, after: number) {
  const [data, setData] = useState<{ id: string; around: Around | null; error: boolean } | null>(null)
  useEffect(() => {
    if (!id) return
    let live = true
    fetchAround(id, before, after)
      .then((around) => live && setData({ id, around, error: false }))
      .catch(() => live && setData({ id, around: null, error: true }))
    return () => {
      live = false
    }
  }, [id, before, after])
  return data && data.id === id ? data : null
}

const SAID_NOTHING = (n: Around['messages'][number]) => (n.files ? 'sent a file' : 'said nothing in words')

/** "open": click shows the source in the side panel, hovering shows the lines around it. */
export function OpenSource({ source, highlight, onOpen }: { source: Source; highlight: Set<string>; onOpen: () => void }) {
  const ref = useRef<HTMLButtonElement>(null)
  const [at, setAt] = useState<{ x: number; y: number; above: boolean } | null>(null)
  const timer = useRef<number | undefined>(undefined)
  const peek = useAround(at ? source.id : null, 3, 1)

  const show = () => {
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => {
      const r = ref.current?.getBoundingClientRect()
      if (!r) return
      const above = r.bottom > window.innerHeight - 240
      setAt({ x: Math.min(r.left, window.innerWidth - 400), y: above ? r.top - 6 : r.bottom + 6, above })
    }, 250)
  }
  const hide = () => {
    window.clearTimeout(timer.current)
    setAt(null)
  }
  useEffect(() => () => window.clearTimeout(timer.current), [])

  return (
    <>
      <button
        ref={ref}
        onClick={() => {
          hide()
          onOpen()
        }}
        onMouseEnter={show}
        onMouseLeave={hide}
        onFocus={show}
        onBlur={hide}
        className="underline underline-offset-2 hover:text-gray-200"
      >
        open
      </button>
      {at &&
        createPortal(
          <div
            role="tooltip"
            className="pointer-events-none fixed z-[70] w-[380px] rounded-lg border border-[#3a3b42] bg-[#1d1e22] p-3 shadow-xl"
            style={{ left: at.x, top: at.y, transform: at.above ? 'translateY(-100%)' : undefined }}
          >
            {!peek ? (
              <div className="flex items-center gap-2 text-xs text-muted">
                <Spinner className="size-3.5" /> Loading…
              </div>
            ) : peek.error || !peek.around ? (
              <div className="text-xs text-muted">Couldn’t load the messages around this one.</div>
            ) : (
              <ul className="flex flex-col gap-1.5">
                {peek.around.messages.map((n) => (
                  <li key={n.id} className={cx('text-[12.5px] leading-snug', highlight.has(n.id) ? 'text-gray-100' : 'text-muted')}>
                    <span className="font-semibold">
                      {n.author.display_name || n.author.name}
                    </span>
                    <span className="ml-1.5 font-mono text-[10.5px] text-muted">{fmtTime(n.ts)}</span>
                    <div className={cx('line-clamp-3 break-words', highlight.has(n.id) && 'rounded bg-[#E0B25C]/10 px-1 -mx-1')}>
                      {n.content_text || <i>{SAID_NOTHING(n)}</i>}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>,
          document.body
        )}
    </>
  )
}

export type Preview = { title: string; icon?: ReactNode; sources: Source[]; index: number }

const PANEL_MIN = 320 // narrower than this and the panel gets out of the way instead
const LIST_MIN = 480 // the list beside it never gets squeezed below this
const PANEL_DEFAULT = 480
const WIDTH_KEY = 'curator.previewWidth'

// The panel's slot at the right of the page. Drag its left edge to resize (double-click resets);
// the width is remembered. When the window gets too narrow to fit it beside the list, it hides,
// and only comes back over the list while something is open.
export function SourceAside(props: Parameters<typeof SourcePanel>[0]) {
  const ref = useRef<HTMLElement>(null)
  const [want, setWant] = useState(() => Number(localStorage.getItem(WIDTH_KEY)) || PANEL_DEFAULT)
  // Room for the list and the panel together: from the list's left edge to the page's right edge.
  const [room, setRoom] = useState(Infinity)
  useEffect(() => {
    const aside = ref.current
    const parent = aside?.parentElement
    if (!aside || !parent) return
    const measure = () => {
      const list = aside.previousElementSibling as HTMLElement | null
      setRoom(parent.clientWidth - (list?.offsetLeft ?? 0))
    }
    const ro = new ResizeObserver(measure)
    ro.observe(parent)
    return () => ro.disconnect()
  }, [])

  const maxWidth = room - LIST_MIN
  const docked = Math.min(want, maxWidth) >= PANEL_MIN
  const width = docked ? Math.min(want, maxWidth) : Math.min(want, room)
  const clamp = (w: number) => Math.round(Math.max(PANEL_MIN, Math.min(w, maxWidth)))
  const save = (w: number) => localStorage.setItem(WIDTH_KEY, String(w))

  const drag = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return
    e.preventDefault()
    const handle = e.currentTarget
    const startX = e.clientX
    const start = width
    let last = start
    handle.setPointerCapture(e.pointerId)
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    const move = (ev: PointerEvent) => setWant((last = clamp(start + startX - ev.clientX)))
    const up = () => {
      handle.removeEventListener('pointermove', move)
      handle.removeEventListener('pointerup', up)
      handle.removeEventListener('pointercancel', up)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      save(last)
    }
    handle.addEventListener('pointermove', move)
    handle.addEventListener('pointerup', up)
    handle.addEventListener('pointercancel', up)
  }

  return (
    <aside
      ref={ref}
      aria-label="Where this came from"
      style={{ width }}
      className={cx(
        'h-full shrink-0 border-l border-[#2f3037] bg-page',
        docked ? 'relative' : 'absolute inset-y-0 right-0 z-20 shadow-2xl',
        !docked && !props.preview && 'hidden'
      )}
    >
      {docked && (
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize preview"
          aria-valuemin={PANEL_MIN}
          aria-valuemax={Math.round(maxWidth)}
          aria-valuenow={Math.round(width)}
          tabIndex={0}
          title="Drag to resize. Double-click to reset."
          onPointerDown={drag}
          onDoubleClick={() => {
            setWant(PANEL_DEFAULT)
            save(PANEL_DEFAULT)
          }}
          onKeyDown={(e) => {
            const step = e.shiftKey ? 64 : 16
            const d = e.key === 'ArrowLeft' ? step : e.key === 'ArrowRight' ? -step : 0
            if (!d) return
            e.preventDefault()
            const w = clamp(width + d)
            setWant(w)
            save(w)
          }}
          className="group absolute inset-y-0 -left-[3px] z-10 w-[6px] cursor-col-resize outline-none"
        >
          <span className="mx-auto block h-full w-px bg-transparent transition-colors group-hover:bg-gray-500 group-focus-visible:bg-gray-300 group-active:bg-gray-300" />
        </div>
      )}
      <SourcePanel {...props} />
    </aside>
  )
}

export function SourcePanel({ preview, onIndex, onClose, openMessage }: {
  preview: Preview | null
  onIndex: (i: number) => void
  onClose: () => void
  openMessage: (id: string) => void
}) {
  const source = preview?.sources[preview.index] ?? null
  const data = useAround(source?.id ?? null, 12, 6)
  const highlight = useMemo(() => new Set(preview?.sources.map((s) => s.id)), [preview])
  const focus = useRef<HTMLLIElement>(null)
  useEffect(() => {
    focus.current?.scrollIntoView({ block: 'center' })
  }, [data])

  if (!preview || !source) {
    return (
      <div className="grid h-full place-items-center px-10 text-center text-sm text-muted">
        Press “open” on a memory or term to see the messages it came from here.
      </div>
    )
  }
  const n = preview.sources.length
  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex items-start gap-3 border-b border-[#2f3037] px-5 pt-5 pb-4">
        {preview.icon && <span className="mt-0.5 flex">{preview.icon}</span>}
        <p className="min-w-0 flex-1 text-[14px] leading-snug text-gray-100">{preview.title}</p>
        <button title="Close" aria-label="Close preview" onClick={onClose} className="text-muted hover:text-gray-100">
          <LuX className="size-4" />
        </button>
      </header>
      <div className="flex items-center gap-2 border-b border-[#2f3037] px-5 py-2.5 text-xs text-muted">
        {n > 1 ? (
          <div className="flex flex-wrap items-center gap-1">
            <span className="mr-1">Source</span>
            {preview.sources.map((s, i) => (
              <button
                key={s.id}
                onClick={() => onIndex(i)}
                title={`${fmtWhen(s.ts)}: ${s.content_text}`}
                className={cx(
                  'h-6 min-w-6 rounded px-1.5 font-mono text-[11px]',
                  i === preview.index ? 'bg-gray-100 text-ink' : 'border border-line hover:bg-raised'
                )}
              >
                {i + 1}
              </button>
            ))}
          </div>
        ) : (
          <span className="font-mono">
            {fmtWhen(source.ts)}
          </span>
        )}
        <span className="flex-1" />
        <button
          onClick={() => openMessage(source.id)}
          className="flex shrink-0 items-center gap-1.5 rounded-md border border-line px-2 py-1 hover:bg-raised hover:text-gray-200"
        >
          <LuExternalLink className="size-3.5" /> Show in Threads
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto px-3 py-3">
        {!data ? (
          <Loading label="Loading messages…" />
        ) : data.error || !data.around ? (
          <p className="py-10 text-center text-sm text-muted">Couldn’t load the messages around this one.</p>
        ) : (
          <ul className="flex flex-col">
            {data.around.messages.map((m, i, all) => {
              const prev = all[i - 1]
              const continued = prev && prev.author_id === m.author_id && m.ts - prev.ts < 5 * 60_000
              const hit = highlight.has(m.id)
              return (
                <li
                  key={m.id}
                  ref={m.id === source.id ? focus : undefined}
                  className={cx(
                    'flex gap-3 rounded-md px-2',
                    continued ? 'py-0.5' : 'mt-2 pt-1.5 pb-0.5',
                    hit && 'bg-[#E0B25C]/10',
                    m.excluded && 'opacity-50'
                  )}
                >
                  <div className="w-7 shrink-0">{!continued && <Avatar who={m.author} size={26} />}</div>
                  <div className="min-w-0 flex-1">
                    {!continued && (
                      <div className="flex items-baseline gap-2">
                        <span className="text-[13px] font-semibold text-gray-100">{m.author.display_name || m.author.name}</span>
                        {m.author.is_bot && <BotTag />}
                        <span className="font-mono text-[10.5px] text-muted">{fmtTime(m.ts)}</span>
                      </div>
                    )}
                    <div className="text-[13.5px] leading-snug break-words whitespace-pre-wrap text-gray-300">
                      {m.content_text || <i className="text-muted">{SAID_NOTHING(m)}</i>}
                      {m.files > 0 && m.content_text && <LuPaperclip className="ml-1.5 inline size-3 text-muted" />}
                    </div>
                  </div>
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </div>
  )
}
