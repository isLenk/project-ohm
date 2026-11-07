import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { LuCheck, LuEye, LuEyeOff, LuFlag, LuMerge, LuX } from 'react-icons/lu'
import { del, patch, post, put, type Block, type ConversationStats, type EditResult, type Message } from './api'
import type { FeedApi } from './useFeed'
import { Avatar, BotTag } from './Avatar'
import { GUTTER, approveHover, approveMark, chunks, color, cx, fmtDay, fmtRange, fmtTime, nameOf } from './util'

type Props = {
  feed: FeedApi
  onEdit: (r: EditResult) => void
  onError: (e: unknown) => void
  onCurrent: (c: ConversationStats | null) => void
  /** Join this conversation onto the one before it (same channel). */
  onMerge: (c: ConversationStats) => void
  /** Start a new conversation at this message. */
  onSplit: (messageId: string) => void
  onApprove: (c: ConversationStats) => void
  /** The conversation the header acts on, marked down the left edge. */
  currentId?: number
  /** The conversation an Approve button is hovered for: marked green. */
  approvingId: number | null
  onApproving: (id: number | null) => void
  /** Leave bots' messages out of the feed (they're never trained on anyway). */
  hideBots: boolean
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

// `click`: started by clicking a message instead of dragging a dot; the next click picks the target.
type Drag = { kind: 'block' | 'message'; unitId: string; from: string; ts: number; blockId: string; x: number; y: number; click?: boolean }

const HEAD = 9
// Arrow trunks: first lane's x in the gutter, distance between lanes, and how many fit before wrapping.
// The first lane clears the drag handles at x=24.
const LANE_X0 = 36
const LANE_STEP = 10
const LANES = Math.floor((GUTTER - 8 - LANE_X0) / LANE_STEP) + 1
const LANE_GAP = 10 // px two trunks in one lane must stay apart

export default function ThreadsView({ feed, onEdit, onError, onCurrent, onMerge, onSplit, onApprove, currentId, approvingId, onApproving, hideBots }: Props) {
  const scroller = useRef<HTMLDivElement>(null)
  const content = useRef<HTMLDivElement>(null)
  const gutter = useRef<HTMLDivElement>(null)
  const anchors = useRef(new Map<string, HTMLElement>())
  const [pos, setPos] = useState<Map<string, number>>(new Map())
  const [height, setHeight] = useState(0)
  const [hover, setHover] = useState<string | null>(null)
  const [hoverBlock, setHoverBlock] = useState<string | null>(null)
  // A message under the pointer: if others reply to it, their arrows and lines light up.
  const [hoverTarget, setHoverTarget] = useState<string | null>(null)
  const [drag, setDrag] = useState<Drag | null>(null)
  const [dropTarget, setDropTarget] = useState<{ id: string; ok: boolean } | null>(null)
  const [flash, setFlash] = useState<string | null>(null)
  const pendingUp = useRef<number | null>(null)
  // After a jump, the jumped-to conversation stays current until the user scrolls themselves.
  // Otherwise the scroll tracker picks whatever conversation starts above the centred message.
  const follow = useRef(true)
  const userScroll = useCallback(() => {
    follow.current = true
  }, [])

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
  }, [feed.items, hideBots, measure])

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
      follow.current = false
      let owner: ConversationStats | null = null
      for (const it of feed.items) {
        if (it.type === 'conversation_start') owner = it.conversation
        else if (it.messages.some((m) => m.id === feed.jump!.messageId)) break
      }
      if (owner) onCurrent(owner)
      if (feed.jump.top) {
        ;(anchors.current.get(`c:${owner?.id}`) ?? el).scrollIntoView({ block: 'start' })
        return undefined
      }
      el.scrollIntoView({ block: 'center' })
      setFlash(feed.jump.messageId)
      const t = setTimeout(() => setFlash(null), 1600)
      return () => clearTimeout(t)
    }
    return undefined
  }, [feed.jump, pos.size]) // eslint-disable-line react-hooks/exhaustive-deps

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
    if (follow.current) onCurrent(current)
  }, [feed, onCurrent])

  useEffect(() => {
    onScroll()
  }, [feed.items, hideBots]) // eslint-disable-line react-hooks/exhaustive-deps

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


  // Which conversation each item belongs to, whether it's approved, and where its run starts/ends.
  // What's drawn: bots' blocks go when hideBots is on (a bot's messages are always its own blocks).
  const items = useMemo(
    () => (hideBots ? feed.items.filter((it) => it.type !== 'block' || !feed.authors[it.author_id]?.is_bot) : feed.items),
    [feed.items, feed.authors, hideBots]
  )
  const chunk = useMemo(() => chunks(items), [items])

  // Everything replying to the hovered message: their arrows, and each reply's lines with its author's colour.
  const replies = useMemo(() => {
    if (!hoverTarget || drag) return null
    const incoming = arrows.filter((a) => a.to === hoverTarget)
    if (!incoming.length) return null
    const lines = new Map<string, string>() // message id -> colour
    for (const a of incoming) {
      if (a.kind === 'message') lines.set(a.unitId, color(a.colorIndex))
      else
        feed.blocks
          .find((b) => b.id === a.unitId)
          ?.messages.filter((m) => !m.link)
          .forEach((m) => lines.set(m.id, color(a.colorIndex)))
    }
    return { arrows: new Set(incoming.map((a) => a.key)), lines }
  }, [hoverTarget, drag, arrows, feed.blocks])

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

  // Clicking a message starts an arrow from its block (Alt: from just that line); the next click picks
  // what it replies to. A line that's already split off is its own unit either way.
  const startLink = (e: React.MouseEvent, b: Block, m: Message) => {
    if (drag || (e.target as HTMLElement).closest('button, a')) return
    if (window.getSelection()?.toString()) return // selecting text to copy, not linking
    const g = gutter.current!.getBoundingClientRect()
    const at = { x: e.clientX - g.left, y: e.clientY - g.top, click: true }
    if (m.link || e.altKey) {
      setDrag({ kind: 'message', unitId: m.id, from: `m:${m.id}`, ts: m.ts, blockId: b.id, ...at })
    } else {
      const own = b.messages.filter((x) => !x.link)
      setDrag({ kind: 'block', unitId: b.id, from: `b:${b.id}`, ts: own[0]?.ts ?? m.ts, blockId: b.id, ...at })
    }
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
    const up = (e: PointerEvent | MouseEvent) => {
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
    // Click mode finishes on the next click, caught before it reaches the message (or it would start
    // a new arrow there). Clicking anything that isn't a valid target cancels.
    const pick = (e: MouseEvent) => {
      e.preventDefault()
      e.stopPropagation()
      up(e)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('keydown', key)
    if (drag.click) window.addEventListener('click', pick, true)
    else window.addEventListener('pointerup', up)
    return () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('keydown', key)
      window.removeEventListener('click', pick, true)
      window.removeEventListener('pointerup', up)
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

  // ── arrow layout ────────────────────────────────────────────────────────
  // Arrows into the same message share one vertical trunk (one for confirmed, one for suggested) and branch off
  // to each reply with a rounded corner. Trunks get lanes by overlap: the shortest sit nearest the
  // text, and a lane only takes a trunk that clears the others already in it. Paths are drawn from the
  // target outwards so dashes line up where arrows share the trunk.
  const layout = useMemo(() => {
    type Group = { key: string; to: string; arrows: { a: Arrow; ys: number }[]; yt: number; stub: boolean; top: number; bottom: number }
    const groups = new Map<string, Group>()
    for (const a of arrows) {
      const ys = pos.get(a.from)
      if (ys == null) continue
      if (hideBots && feed.authors[messages.get(a.to)?.author_id ?? '']?.is_bot) continue // replies to a hidden bot
      // Confirmed and suggested arrows into one message get separate trunks, so a guess never hides
      // behind (or under) a confirmed reply.
      const key = `${a.to}|${a.suggested ? 'suggested' : 'confirmed'}`
      const g = groups.get(key) ?? { key, to: a.to, arrows: [], yt: 0, stub: false, top: 0, bottom: 0 }
      g.arrows.push({ a, ys })
      groups.set(key, g)
    }
    for (const g of groups.values()) {
      const ys = g.arrows.map((x) => x.ys)
      const yt = pos.get(`m:${g.to}`)
      g.stub = yt == null // parent scrolled out of the loaded page: rise to a labelled stub
      g.yt = yt ?? Math.max(14, Math.min(...ys) - 46)
      g.top = Math.min(g.yt, ...ys)
      g.bottom = Math.max(g.yt, ...ys)
    }
    const lanes: [number, number][][] = []
    const laneOf = new Map<string, number>()
    for (const g of [...groups.values()].sort((x, y) => x.bottom - x.top - (y.bottom - y.top))) {
      let k = lanes.findIndex((l) => l.every(([t, b]) => g.bottom + LANE_GAP < t || g.top - LANE_GAP > b))
      if (k < 0) k = lanes.push([]) - 1
      lanes[k].push([g.top, g.bottom])
      laneOf.set(g.key, k)
    }
    const out = new Map<
      string,
      { d: string; hit: string; head: string | null; apex: { x: number; y: number }; stub: number | null; span: number; to: string }
    >()
    for (const g of groups.values()) {
      const L = LANE_X0 + (laneOf.get(g.key)! % LANES) * LANE_STEP
      const yt = g.yt
      for (const { a, ys } of g.arrows) {
        const s = ys >= yt ? 1 : -1
        const r = Math.min(6, Math.abs(ys - yt) / 2)
        const start = g.stub ? `M ${L} ${yt}` : `M ${10 + HEAD} ${yt} H ${L - r} Q ${L} ${yt} ${L} ${yt + s * r}`
        // This arrow's own stretch of the shared trunk: up to the next reply nearer the target.
        let stop = yt
        for (const o of g.arrows) {
          if (o.a !== a && (o.ys - yt) * s > 0 && (ys - o.ys) * s > 0 && Math.abs(ys - o.ys) < Math.abs(ys - stop)) stop = o.ys
        }
        out.set(a.key, {
          d: `${start} V ${ys - s * r} Q ${L} ${ys} ${L - r} ${ys} H 10`,
          hit: `M 10 ${ys} H ${L} V ${stop}`,
          head: g.stub ? null : `10,${yt} ${10 + HEAD},${yt - 5} ${10 + HEAD},${yt + 5}`,
          apex: { x: L, y: (ys + stop) / 2 },
          stub: g.stub ? yt : null,
          span: Math.abs(ys - yt),
          to: g.to
        })
      }
    }
    return out
  }, [arrows, pos, hideBots, feed.authors, messages])

  // Longest first, so where arrows share a trunk the nearer reply's colour is on top.
  const drawOrder = useMemo(
    () => arrows.filter((a) => layout.has(a.key)).sort((x, y) => layout.get(y.key)!.span - layout.get(x.key)!.span),
    [arrows, layout]
  )

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
  const hoveredPath = hovered ? layout.get(hovered.key) : null
  const dragFrom = drag ? pos.get(drag.from) : null

  return (
    <div
      ref={scroller}
      onScroll={onScroll}
      onWheel={userScroll}
      onPointerDown={userScroll}
      onTouchStart={userScroll}
      onKeyDown={userScroll}
      className="min-h-0 flex-1 overflow-auto"
    >
      <div
        ref={content}
        className={cx('relative mx-auto px-6 pb-24', drag && (drag.click ? 'cursor-crosshair select-none' : 'cursor-grabbing select-none'))}
        style={{ maxWidth: 1040, paddingRight: GUTTER + 24 }}
      >
        {feed.prev && <div className="py-3 text-center text-xs text-muted">Scroll up for earlier messages</div>}
        {drag?.click && (
          <div className="pointer-events-none fixed bottom-6 left-1/2 z-30 -translate-x-1/2 rounded-md bg-gray-100 px-3 py-1.5 text-[12.5px] font-semibold text-ink">
            Click the message this replies to · Esc cancels
          </div>
        )}
        {items.map((it) => {
          if (it.type === 'conversation_start') {
            const c = it.conversation
            const idx = items.indexOf(it)
            let before: number | null = null // last message time before this conversation, if loaded
            for (let j = idx - 1; j >= 0; j--) {
              const p = items[j]
              if (p.type === 'block' && p.messages.length) {
                before = p.messages[p.messages.length - 1].ts
                break
              }
            }
            const hasAbove = before != null || !!feed.prev
            const gapMin = before != null ? Math.round((c.first_ts - before) / 60_000) : null
            return (
              <div
                key={`c${c.id}`}
                ref={register(`c:${c.id}`)}
                className={cx('group/conv mt-4 flex items-center gap-3 pt-2 pb-2 text-[12.5px]', approveMark(chunk.get(`c${c.id}`), currentId, approvingId))}
              >
                <span className="font-semibold text-gray-100">
                  {fmtDay(c.first_ts)} · {fmtRange(c.first_ts, c.last_ts)}
                </span>
                {c.approved && <LuCheck className="size-3.5 text-[#6CCB98]" aria-label="Approved" />}
                {gapMin != null && (
                  <span className="text-muted" title="Silence before this conversation started">
                    {gapMin >= 120 ? `${Math.round(gapMin / 60)} h` : `${gapMin} min`} gap
                  </span>
                )}
                {hasAbove && (
                  <button
                    onClick={() => onMerge(c)}
                    title="Still the same conversation? Join it onto the one above. Clears approval on the joined conversation; Ctrl+Z undoes it."
                    className="flex items-center gap-1 rounded border border-line px-2 py-0.5 text-gray-300 opacity-0 group-hover/conv:opacity-100 hover:bg-raised hover:text-white focus:opacity-100"
                  >
                    <LuMerge className="size-3.5" /> Merge with above
                  </button>
                )}
                {!c.approved && (
                  <button
                    onClick={() => {
                      onApproving(null) // the button goes away, so it never gets a mouseleave
                      onApprove(c)
                    }}
                    {...approveHover(c.id, onApproving)}
                    title="Approve this conversation for training"
                    className="flex items-center gap-1 rounded border border-line px-2 py-0.5 text-gray-300 opacity-0 group-hover/conv:opacity-100 hover:border-[#6CCB98]/60 hover:bg-raised hover:text-white focus:opacity-100"
                  >
                    <LuCheck className="size-3.5" /> Approve
                  </button>
                )}
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
          const above = items[items.indexOf(it) - 1]
          const prevTs = above?.type === 'block' && above.messages.length ? above.messages[above.messages.length - 1].ts : null
          const parent = b.link?.parent_id ? messages.get(b.link.parent_id) : null
          const ownCount = b.messages.filter((m) => !m.link).length
          const loose = !b.link && b.messages.some((m) => !m.link && !m.excluded)
          return (
            <div
              key={`b${b.id}`}
              className={cx('group/block relative flex gap-3.5 py-2', approveMark(chunk.get(`b${b.id}`), currentId, approvingId))}
              onMouseEnter={() => setHoverBlock(b.id)}
              onMouseLeave={() => setHoverBlock((h) => (h === b.id ? null : h))}
            >
              {/* the gap above a block that continues its conversation: offer to start a new one here */}
              {first && !chunk.get(`b${b.id}`)?.top && !drag && (
                <div className="group/split absolute inset-x-0 -top-2 z-10 flex h-4 items-center gap-2 pl-[46px] opacity-0 hover:opacity-100">
                  <span className="h-0 flex-1 border-t border-dashed border-[#4a4d55]" />
                  <button
                    onClick={() => onSplit(first.id)}
                    title="Start a new conversation here. Ctrl+Z undoes it."
                    className="rounded border border-line bg-page px-2 py-0.5 text-xs text-gray-200 hover:bg-raised"
                  >
                    Split here
                  </button>
                  {prevTs != null && first.ts - prevTs > 60_000 && (
                    <span className="font-mono text-[11px] text-muted">{Math.round((first.ts - prevTs) / 60_000)} min gap</span>
                  )}
                  <span className="h-0 w-6 border-t border-dashed border-[#4a4d55]" />
                </div>
              )}
              <Avatar who={author} colorIndex={b.color_index} size={32} className="mt-[3px]" />
              <div className="min-w-0 flex-1">
                {b.link?.origin === 'discord' && b.link.parent_id && (
                  <div className="truncate text-[12.5px] leading-[22px] text-muted">
                    ↱ {nameOf(feed.authors[parent?.author_id || b.link.parent_author_id || ''])}:{' '}
                    {parent?.content_text ?? b.link.parent_preview}
                  </div>
                )}
                <div ref={register(`b:${b.id}`)} className="flex items-baseline gap-2.5 leading-[22px]">
                  <span className="text-[14px] font-semibold text-gray-100">{nameOf(author)}</span>
                  {!!author?.is_bot && <BotTag />}
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
                    onClick={(e) => startLink(e, b, m)}
                    onMouseEnter={() => setHoverTarget(m.id)}
                    onMouseLeave={() => setHoverTarget((h) => (h === m.id ? null : h))}
                    className={cx(
                      'group/line -mx-2 flex items-start gap-2 rounded px-2 text-[14.5px] leading-6',
                      m.excluded ? 'text-muted line-through' : 'text-[#D6D7DB]',
                      flash === m.id && 'bg-[#2B3A55]',
                      dropTarget?.id === m.id && (dropTarget.ok ? 'bg-[#26364F]' : 'bg-[#3a2323]'),
                      replies && hoverTarget === m.id && 'bg-[#2a2c33] text-white',
                      m.link && 'underline decoration-2 underline-offset-4'
                    )}
                    style={{
                      ...(m.link ? { textDecorationColor: color(b.color_index) } : {}),
                      // a reply to the hovered message: tint it in the replier's colour
                      ...(replies?.lines.has(m.id)
                        ? { backgroundColor: `${replies.lines.get(m.id)}24` }
                        : {})
                    }}
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
            {(() => {
              const isOn = (a: Arrow) => hover === a.key || !!replies?.arrows.has(a.key)
              // highlighted arrows last, so they sit on top of any trunk they share
              const order = [...drawOrder.filter((a) => !isOn(a)), ...drawOrder.filter(isOn)]
              return order.map((a) => {
                const p = layout.get(a.key)!
                const c = color(a.colorIndex)
                const on = isOn(a)
                return (
                  <g key={a.key} opacity={replies && !on ? 0.2 : 1}>
                    <path d={p.d} fill="none" stroke={c} strokeWidth={on ? 3.5 : 2} strokeDasharray={a.suggested ? '5 5' : undefined} />
                    {p.head && <polygon points={p.head} fill={c} />}
                  </g>
                )
              })
            })()}
            {/* hit areas: each arrow's own branch and stretch of trunk, so shared trunks stay clickable per reply */}
            {drawOrder.map((a) => (
              <path
                key={`hit${a.key}`}
                d={layout.get(a.key)!.hit}
                fill="none"
                stroke="transparent"
                strokeWidth={10}
                style={{ pointerEvents: drag ? 'none' : 'stroke', cursor: 'pointer' }}
                onMouseEnter={() => setHover(a.key)}
                onMouseLeave={() => setHover((h) => (h === a.key && !a.suggested ? null : h))}
                onClick={() => detach(a)}
              />
            ))}
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
          {/* one label per out-of-page parent, however many replies share its trunk */}
          {arrows.map((a, i) => {
            const p = layout.get(a.key)
            if (!p || p.stub == null || !a.preview || arrows.findIndex((o) => o.to === a.to && layout.has(o.key)) !== i) return null
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
