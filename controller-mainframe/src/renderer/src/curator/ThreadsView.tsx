import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { LuCheck, LuEye, LuEyeOff, LuFlag, LuX } from 'react-icons/lu'
import { del, patch, post, put, type Block, type ConversationStats, type EditResult, type Message } from './api'
import type { FeedApi } from './useFeed'
import { GUTTER, color, cx, fmtDay, fmtRange, fmtTime, lane, nameOf } from './util'

type Props = {
  feed: FeedApi
  onEdit: (r: EditResult) => void
  onError: (e: unknown) => void
  onCurrent: (c: ConversationStats | null) => void
}

type Arrow = {
  key: string
  kind: 'block' | 'message'
  unitId: string
  from: string // anchor key of the source
  to: string // parent message id
  colorIndex: number
  suggested: boolean
  split: boolean
  preview: { ts: number; text: string } | null
}

type Drag = { kind: 'block' | 'message'; unitId: string; from: string; ts: number; blockId: string; x: number; y: number }

const HEAD = 9

export default function ThreadsView({ feed, onEdit, onError, onCurrent }: Props) {
  const scroller = useRef<HTMLDivElement>(null)
  const content = useRef<HTMLDivElement>(null)
  const gutter = useRef<HTMLDivElement>(null)
  const anchors = useRef(new Map<string, HTMLElement>())
  const [pos, setPos] = useState<Map<string, number>>(new Map())
  const [height, setHeight] = useState(0)
  const [hover, setHover] = useState<string | null>(null)
  const [hoverBlock, setHoverBlock] = useState<string | null>(null)
  const [drag, setDrag] = useState<Drag | null>(null)
  const [dropTarget, setDropTarget] = useState<{ id: string; ok: boolean } | null>(null)
  const [flash, setFlash] = useState<string | null>(null)
  const pendingUp = useRef<number | null>(null)

  const register = useCallback(
    (key: string) => (el: HTMLElement | null) => {
      if (el) anchors.current.set(key, el)
      else anchors.current.delete(key)
    },
    []
  )

  const messages = useMemo(() => {
    const m = new Map<string, Message & { author_id: string; block_id: string }>()
    for (const b of feed.blocks) for (const msg of b.messages) m.set(msg.id, { ...msg, author_id: b.author_id, block_id: b.id })
    return m
  }, [feed.blocks])

  // ── measure anchor positions (y centre relative to the content box) ─────
  const measure = useCallback(() => {
    const box = content.current?.getBoundingClientRect()
    if (!box) return
    const next = new Map<string, number>()
    anchors.current.forEach((el, key) => {
      const r = el.getBoundingClientRect()
      next.set(key, r.top - box.top + r.height / 2)
    })
    setPos(next)
    setHeight(content.current!.scrollHeight)
  }, [])

  useLayoutEffect(() => {
    if (pendingUp.current != null && scroller.current) {
      scroller.current.scrollTop += scroller.current.scrollHeight - pendingUp.current
      pendingUp.current = null
    }
    measure()
  }, [feed.items, measure])

  useEffect(() => {
    if (!content.current) return
    const ro = new ResizeObserver(() => measure())
    ro.observe(content.current)
    return () => ro.disconnect()
  }, [measure])

  // ── jump to a message ───────────────────────────────────────────────────
  useEffect(() => {
    if (!feed.jump) return
    const el = anchors.current.get(`m:${feed.jump.messageId}`)
    if (el) {
      el.scrollIntoView({ block: 'center' })
      setFlash(feed.jump.messageId)
      const t = setTimeout(() => setFlash(null), 1600)
      return () => clearTimeout(t)
    }
    return undefined
  }, [feed.jump, pos.size])

  // ── scroll: load more, track the current conversation ───────────────────
  const onScroll = useCallback(() => {
    const s = scroller.current
    if (!s) return
    if (s.scrollTop + s.clientHeight > s.scrollHeight - 600 && feed.next) feed.more('down')
    if (s.scrollTop < 300 && feed.prev && pendingUp.current == null) {
      pendingUp.current = s.scrollHeight
      feed.more('up').then((loaded) => {
        if (!loaded) pendingUp.current = null
      })
    }
    const top = s.getBoundingClientRect().top + 8
    let current: ConversationStats | null = null
    for (const it of feed.items) {
      if (it.type !== 'conversation_start') continue
      const el = anchors.current.get(`c:${it.conversation.id}`)
      if (el && el.getBoundingClientRect().top <= top) current = it.conversation
      else if (!current) current = it.conversation
    }
    onCurrent(current)
  }, [feed, onCurrent])

  useEffect(() => {
    onScroll()
  }, [feed.items]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── arrows ──────────────────────────────────────────────────────────────
  const arrows = useMemo(() => {
    const out: Arrow[] = []
    for (const b of feed.blocks) {
      if (b.link?.parent_id && b.link.status !== 'root') {
        out.push({
          key: `block:${b.id}`, kind: 'block', unitId: b.id, from: `b:${b.id}`, to: b.link.parent_id,
          colorIndex: b.color_index, suggested: b.link.status === 'suggested', split: false,
          preview: b.link.parent_preview ? { ts: b.link.parent_ts!, text: b.link.parent_preview } : null
        })
      }
      for (const m of b.messages) {
        if (m.link?.parent_id && m.link.status !== 'root') {
          out.push({
            key: `message:${m.id}`, kind: 'message', unitId: m.id, from: `m:${m.id}`, to: m.link.parent_id,
            colorIndex: b.color_index, suggested: m.link.status === 'suggested', split: true,
            preview: m.link.parent_preview ? { ts: m.link.parent_ts!, text: m.link.parent_preview } : null
          })
        }
      }
    }
    return out
  }, [feed.blocks])

  // Spread several arrows landing on one message so the heads don't overlap.
  const fan = useMemo(() => {
    const byTarget = new Map<string, Arrow[]>()
    for (const a of arrows) byTarget.set(a.to, [...(byTarget.get(a.to) || []), a])
    const off = new Map<string, number>()
    byTarget.forEach((list) => list.forEach((a, i) => off.set(a.key, (i - (list.length - 1) / 2) * 7)))
    return off
  }, [arrows])

  // ── edits ───────────────────────────────────────────────────────────────
  const run = useCallback(
    async (p: Promise<EditResult>) => {
      try {
        onEdit(await p)
      } catch (e) {
        onError(e)
      }
    },
    [onEdit, onError]
  )

  const detach = (a: Arrow) => run(del(`/links/${a.kind}/${a.unitId}`))
  const accept = (a: Arrow) => run(post(`/links/${a.kind}/${a.unitId}/accept`))
  const reject = (a: Arrow) => run(post(`/links/${a.kind}/${a.unitId}/reject`))
  const markRoot = (b: Block) => run(put(`/links/block/${b.id}`, { root: true }))
  const toggleExclude = async (m: Message) => {
    try {
      const r = await patch(`/messages/${m.id}`, { excluded: !m.excluded })
      onEdit(r)
    } catch (e) {
      onError(e)
    }
  }

  // ── dragging a dot onto a message ───────────────────────────────────────
  const startDrag = (e: React.PointerEvent, d: Omit<Drag, 'x' | 'y'>) => {
    e.preventDefault()
    e.stopPropagation()
    const g = gutter.current!.getBoundingClientRect()
    setDrag({ ...d, x: e.clientX - g.left, y: e.clientY - g.top })
  }

  useEffect(() => {
    if (!drag) return
    const target = (x: number, y: number) => {
      const el = document.elementFromPoint(x, y)?.closest('[data-mid]') as HTMLElement | null
      if (!el) return null
      const id = el.dataset.mid!
      const msg = messages.get(id)
      if (!msg) return null
      const ownLine = drag.kind === 'message' && id === drag.unitId
      const sameBlock = msg.block_id === drag.blockId
      const ok = (drag.kind === 'message' && sameBlock && !ownLine) || (msg.ts < drag.ts && !sameBlock)
      return { id, ok, sameBlock }
    }
    const move = (e: PointerEvent) => {
      const g = gutter.current!.getBoundingClientRect()
      setDrag((d) => d && { ...d, x: e.clientX - g.left, y: e.clientY - g.top })
      const t = target(e.clientX, e.clientY)
      setDropTarget(t ? { id: t.id, ok: t.ok } : null)
    }
    const up = (e: PointerEvent) => {
      const t = target(e.clientX, e.clientY)
      const d = drag
      setDrag(null)
      setDropTarget(null)
      if (!t || !t.ok) return
      if (d.kind === 'message' && t.sameBlock) {
        // dropped back on its own block: follow the block again
        const msg = messages.get(d.unitId)
        if (msg?.link) run(del(`/links/message/${d.unitId}`))
        return
      }
      run(put(`/links/${d.kind}/${d.unitId}`, { parent_id: t.id }))
    }
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setDrag(null)
        setDropTarget(null)
      }
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
    window.addEventListener('keydown', key)
    return () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      window.removeEventListener('keydown', key)
    }
  }, [drag, messages, run])

  // ── rendering helpers ───────────────────────────────────────────────────
  const mentionRe = useMemo(() => {
    const names = Object.values(feed.authors)
      .flatMap((a) => [a.name, a.display_name])
      .filter(Boolean)
      .sort((a, b) => b!.length - a!.length)
      .map((n) => n!.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
    return names.length ? new RegExp(`(@(?:${names.join('|')}))`, 'g') : null
  }, [feed.authors])

  const renderText = (text: string) => {
    if (!mentionRe) return text
    return text.split(mentionRe).map((part, i) =>
      i % 2 ? (
        <span key={i} className="text-[#9DB4FF]">
          {part}
        </span>
      ) : (
        part
      )
    )
  }

  const path = (a: Arrow) => {
    const ys = pos.get(a.from)
    if (ys == null) return null
    const L = lane(a.colorIndex) - (a.split ? 18 : 0)
    const off = fan.get(a.key) || 0
    const ytRaw = pos.get(`m:${a.to}`)
    if (ytRaw == null) {
      // parent scrolled out of the loaded page: rise to a labelled stub
      const top = Math.max(14, ys - 46)
      return { d: `M10 ${ys} C ${L} ${ys}, ${L} ${top + 8}, ${L} ${top}`, head: null, apex: { x: L, y: top }, ys, stub: top }
    }
    const yt = ytRaw + off
    return {
      d: `M10 ${ys} C ${L} ${ys}, ${L} ${yt}, ${HEAD + 10} ${yt}`,
      head: `10,${yt} ${10 + HEAD},${yt - 5} ${10 + HEAD},${yt + 5}`,
      apex: { x: 10 + 0.75 * (L - 10), y: (ys + yt) / 2 },
      ys,
      stub: null as number | null
    }
  }

  const unitDots = useMemo(() => {
    const dots: { key: string; anchor: string; kind: 'block' | 'message'; unitId: string; blockId: string; ts: number; state: 'linked' | 'root' | 'loose' | 'split'; colorIndex: number }[] = []
    for (const b of feed.blocks) {
      const own = b.messages.filter((m) => !m.link)
      const live = own.filter((m) => !m.excluded)
      if (own.length) {
        const state = b.link ? (b.link.status === 'root' ? 'root' : 'linked') : live.length ? 'loose' : null
        if (state) dots.push({ key: `b:${b.id}`, anchor: `b:${b.id}`, kind: 'block', unitId: b.id, blockId: b.id, ts: own[0].ts, state, colorIndex: b.color_index })
      }
      for (const m of b.messages) {
        if (m.link) dots.push({ key: `m:${m.id}`, anchor: `m:${m.id}`, kind: 'message', unitId: m.id, blockId: b.id, ts: m.ts, state: m.link.status === 'root' ? 'root' : 'split', colorIndex: b.color_index })
      }
    }
    return dots
  }, [feed.blocks])

  const hovered = arrows.find((a) => a.key === hover) || null
  const hoveredPath = hovered ? path(hovered) : null
  const dragFrom = drag ? pos.get(drag.from) : null

  return (
    <div ref={scroller} onScroll={onScroll} className="min-h-0 flex-1 overflow-auto">
      <div
        ref={content}
        className={cx('relative mx-auto px-6 pb-24', drag && 'cursor-grabbing select-none')}
        style={{ maxWidth: 1040, paddingRight: GUTTER + 24 }}
      >
        {feed.prev && <div className="py-3 text-center text-xs text-muted">Scroll up for earlier messages</div>}
        {feed.items.map((it) => {
          if (it.type === 'conversation_start') {
            const c = it.conversation
            return (
              <div key={`c${c.id}`} ref={register(`c:${c.id}`)} className="flex items-center gap-3 pt-6 pb-2 text-[12.5px]">
                <span className="font-semibold text-gray-100">
                  {fmtDay(c.first_ts)} · {fmtRange(c.first_ts, c.last_ts)}
                </span>
                {c.approved && <LuCheck className="size-3.5 text-[#6CCB98]" aria-label="Approved" />}
                <span className="h-px flex-1 bg-[#3f4049]" />
                <span className="text-muted">
                  {c.people} people · {c.messages} msgs
                </span>
              </div>
            )
          }
          const b = it
          const author = feed.authors[b.author_id]
          const first = b.messages[0]
          const parent = b.link?.parent_id ? messages.get(b.link.parent_id) : null
          const ownCount = b.messages.filter((m) => !m.link).length
          const loose = !b.link && b.messages.some((m) => !m.link && !m.excluded)
          return (
            <div
              key={`b${b.id}`}
              className="group/block relative flex gap-3.5 py-2"
              onMouseEnter={() => setHoverBlock(b.id)}
              onMouseLeave={() => setHoverBlock((h) => (h === b.id ? null : h))}
            >
              <span
                className="mt-[3px] grid size-8 shrink-0 place-items-center rounded-full text-xs font-bold text-ink"
                style={{ background: color(b.color_index) }}
              >
                {nameOf(author, '?').slice(0, 1).toUpperCase()}
              </span>
              <div className="min-w-0 flex-1">
                {b.link?.origin === 'discord' && b.link.parent_id && (
                  <div className="truncate text-[12.5px] leading-[22px] text-muted">
                    ↱ {nameOf(feed.authors[parent?.author_id || b.link.parent_author_id || ''])}:{' '}
                    {parent?.content_text ?? b.link.parent_preview}
                  </div>
                )}
                <div ref={register(`b:${b.id}`)} className="flex items-baseline gap-2.5 leading-[22px]">
                  <span className="text-[14px] font-semibold text-gray-100">{nameOf(author)}</span>
                  <span className="text-[11.5px] text-muted">{first ? fmtTime(first.ts) : ''}</span>
                  {b.link?.status === 'root' && <span className="text-[11.5px] text-muted">thread start</span>}
                  {b.link?.status === 'suggested' && (
                    <span className="text-[11.5px] text-muted">{b.link.origin === 'model' ? "model's guess" : 'suggested reply'}</span>
                  )}
                  {loose && <span className="text-[11.5px] text-[#E0B25C]">not attached</span>}
                  {ownCount > 1 && <span className="text-[11.5px] text-muted">{ownCount} lines</span>}
                  {b.link?.status !== 'root' && (
                    <button
                      title="Mark as thread start"
                      aria-label="Mark as thread start"
                      onClick={() => markRoot(b)}
                      className={cx(
                        'ml-1 grid size-6 place-items-center self-center rounded text-muted hover:bg-raised hover:text-gray-100',
                        loose ? 'opacity-100' : 'opacity-0 group-hover/block:opacity-100'
                      )}
                    >
                      <LuFlag className="size-3.5" />
                    </button>
                  )}
                </div>
                {b.messages.map((m) => (
                  <div
                    key={m.id}
                    data-mid={m.id}
                    ref={register(`m:${m.id}`)}
                    className={cx(
                      'group/line -mx-2 flex items-start gap-2 rounded px-2 text-[14.5px] leading-6',
                      m.excluded ? 'text-muted line-through' : 'text-[#D6D7DB]',
                      flash === m.id && 'bg-[#2B3A55]',
                      dropTarget?.id === m.id && (dropTarget.ok ? 'bg-[#26364F]' : 'bg-[#3a2323]'),
                      m.link && 'underline decoration-2 underline-offset-4'
                    )}
                    style={m.link ? { textDecorationColor: color(b.color_index) } : undefined}
                  >
                    <span className="min-w-0 flex-1 break-words whitespace-pre-wrap">{renderText(m.content_text)}</span>
                    <button
                      title={m.excluded ? 'Include in training' : 'Exclude from training'}
                      aria-label={m.excluded ? 'Include message' : 'Exclude message'}
                      onClick={() => toggleExclude(m)}
                      className="mt-0.5 grid size-5 shrink-0 place-items-center rounded text-muted opacity-0 hover:text-gray-100 group-hover/line:opacity-100"
                    >
                      {m.excluded ? <LuEye className="size-3.5" /> : <LuEyeOff className="size-3.5" />}
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )
        })}
        {feed.next && <div className="py-6 text-center text-xs text-muted">Loading more…</div>}
        {!feed.items.length && !feed.loading && (
          <div className="py-24 text-center text-sm text-muted">No messages yet. Import a Discord export to start.</div>
        )}

        {/* ── gutter with arrows ── */}
        <div ref={gutter} className="absolute top-0 right-6" style={{ width: GUTTER, height }}>
          <svg width={GUTTER} height={height} className="absolute inset-0 overflow-visible" style={{ pointerEvents: 'none' }}>
            {/* block brackets */}
            {feed.blocks.map((b) => {
              const own = b.messages.filter((m) => !m.link)
              if (own.length < 2) return null
              const a = pos.get(`m:${own[0].id}`)
              const z = pos.get(`m:${own[own.length - 1].id}`)
              if (a == null || z == null) return null
              return <path key={`br${b.id}`} d={`M3 ${a - 10}V${z + 10}`} stroke={color(b.color_index)} strokeOpacity={0.5} strokeWidth={2} />
            })}
            {arrows.map((a) => {
              const p = path(a)
              if (!p) return null
              const c = color(a.colorIndex)
              const on = hover === a.key
              return (
                <g key={a.key}>
                  <path d={p.d} fill="none" stroke={c} strokeWidth={on ? 3.5 : 2} strokeDasharray={a.suggested ? '5 5' : undefined} />
                  {p.head && <polygon points={p.head} fill={c} />}
                  <path
                    d={p.d}
                    fill="none"
                    stroke="transparent"
                    strokeWidth={14}
                    style={{ pointerEvents: drag ? 'none' : 'stroke', cursor: 'pointer' }}
                    onMouseEnter={() => setHover(a.key)}
                    onMouseLeave={() => setHover((h) => (h === a.key && !a.suggested ? null : h))}
                    onClick={() => detach(a)}
                  />
                </g>
              )
            })}
            {unitDots.map((d) => {
              const y = pos.get(d.anchor)
              if (y == null) return null
              const c = color(d.colorIndex)
              const common = {
                style: { pointerEvents: 'all' as const, cursor: 'grab' },
                onPointerDown: (e: React.PointerEvent) =>
                  startDrag(e, { kind: d.kind, unitId: d.unitId, from: d.anchor, ts: d.ts, blockId: d.blockId })
              }
              if (d.state === 'linked') return <circle key={d.key} cx={10} cy={y} r={5} fill={c} {...common} />
              if (d.state === 'split') return <circle key={d.key} cx={10} cy={y} r={4} fill="#212229" stroke={c} strokeWidth={2} {...common} />
              if (d.state === 'root') return <circle key={d.key} cx={10} cy={y} r={4.5} fill="#212229" stroke="#5a5d66" strokeWidth={1.5} {...common} />
              return (
                <g key={d.key}>
                  <circle cx={10} cy={y} r={9} fill="#E0B25C" fillOpacity={0.14} />
                  <circle cx={10} cy={y} r={5} fill="#212229" stroke="#E0B25C" strokeWidth={2} {...common} />
                </g>
              )
            })}
            {/* drag-to-split handles on the hovered block's lines */}
            {feed.blocks
              .filter((b) => b.messages.filter((m) => !m.link).length > 1)
              .flatMap((b) =>
                b.messages
                  .filter((m) => !m.link && !m.excluded)
                  .map((m) => {
                    const y = pos.get(`m:${m.id}`)
                    if (y == null) return null
                    return (
                      <circle
                        key={`h${m.id}`}
                        cx={24}
                        cy={y}
                        r={3.5}
                        fill="#212229"
                        stroke={color(b.color_index)}
                        strokeWidth={1.5}
                        opacity={hoverBlock === b.id ? 1 : 0}
                        style={{ pointerEvents: drag ? 'none' : 'all', cursor: 'grab' }}
                        onMouseEnter={() => setHoverBlock(b.id)}
                        onPointerDown={(e) =>
                          startDrag(e, { kind: 'message', unitId: m.id, from: `m:${m.id}`, ts: m.ts, blockId: b.id })
                        }
                      >
                        <title>Drag this line to reply to someone else</title>
                      </circle>
                    )
                  })
              )}
            {drag && dragFrom != null && (
              <path
                d={`M10 ${dragFrom} C 70 ${dragFrom}, 70 ${drag.y}, ${drag.x} ${drag.y}`}
                fill="none"
                stroke="#ECECEE"
                strokeWidth={2}
                strokeDasharray="4 4"
              />
            )}
          </svg>

          {/* stubs for parents outside the loaded page */}
          {arrows.map((a) => {
            const p = path(a)
            if (!p || p.stub == null || !a.preview) return null
            return (
              <button
                key={`stub${a.key}`}
                onClick={() => feed.reset(a.to)}
                className="absolute right-0 max-w-[150px] truncate rounded bg-[#26272B] px-1.5 py-0.5 text-left text-[11px] text-gray-300 hover:text-white"
                style={{ top: p.stub - 22 }}
                title={a.preview.text}
              >
                ↑ {fmtTime(a.preview.ts)} · {a.preview.text}
              </button>
            )
          })}

          {hovered && hoveredPath && !drag && (
            <div
              className="absolute flex items-center gap-1"
              style={{ left: Math.min(hoveredPath.apex.x - 10, GUTTER - 70), top: hoveredPath.apex.y - 12 }}
              onMouseEnter={() => setHover(hovered.key)}
              onMouseLeave={() => setHover(null)}
            >
              {hovered.suggested ? (
                <>
                  <button
                    aria-label="Accept suggested reply"
                    onClick={() => accept(hovered)}
                    className="grid h-6 w-7 place-items-center rounded border border-line bg-page text-[#6CCB98] hover:bg-raised"
                  >
                    <LuCheck className="size-3.5" />
                  </button>
                  <button
                    aria-label="Reject suggested reply"
                    onClick={() => reject(hovered)}
                    className="grid h-6 w-7 place-items-center rounded border border-line bg-page text-gray-300 hover:bg-raised"
                  >
                    <LuX className="size-3.5" />
                  </button>
                </>
              ) : (
                <span className="pointer-events-none rounded bg-gray-100 px-2 py-0.5 text-[11.5px] font-semibold whitespace-nowrap text-ink">
                  Click to detach
                </span>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
