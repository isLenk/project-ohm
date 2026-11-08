import type { Author, FeedItem } from './api'

// One color per person, everywhere: dot, name tick, arrow. Indexed by authors.color_index.
export const PALETTE = ['#5EC8E5', '#A78BFA', '#E8CF4A', '#F47A9B', '#F59E5B', '#6CCB98', '#9AA5B8', '#F28B82']

export const color = (index: number | undefined) => PALETTE[(index ?? 0) % PALETTE.length]

// Width of the Threads gutter the reply arrows run in (lanes are laid out in ThreadsView).
export const GUTTER = 160

export const nameOf = (a: Author | undefined, fallback = 'Unknown') =>
  a ? a.display_name || a.name : fallback

const time = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' })
const day = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' })

export const fmtTime = (ms: number) => time.format(new Date(ms))
export const fmtDay = (ms: number) => day.format(new Date(ms))
const dayYear = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
/** "Nov 5 · 9:18 PM", with the year when it isn't this year: memories can be about a particular day. */
export const fmtWhen = (ms: number) => {
  const d = new Date(ms)
  return `${(d.getFullYear() === new Date().getFullYear() ? day : dayYear).format(d)} · ${time.format(d)}`
}
export const fmtRange = (a: number, b: number) => `${fmtTime(a)} – ${fmtTime(b)}`

export function fmtDuration(seconds: number | null | undefined) {
  if (seconds == null || !isFinite(seconds)) return '—'
  const s = Math.max(0, Math.round(seconds))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const r = s % 60
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}` : `${m}:${String(r).padStart(2, '0')}`
}

export const pct = (x: number) => `${Math.round(x * 100)}%`

// Trainable replies (Ohm's turns) a LoRA wants. Below REPLIES_LOW it mostly learns the few
// conversations it saw instead of how the group talks.
export const REPLIES_AIM = 1500
export const REPLIES_LOW = 800
export const aimHint = (replies: number) => `${replies.toLocaleString()} replies approved · aim for ${REPLIES_AIM.toLocaleString()}+`

export function cx(...parts: (string | false | null | undefined)[]) {
  return parts.filter(Boolean).join(' ')
}

export type ChunkInfo = { id: number | null; approved: boolean; top: boolean; bottom: boolean }

/** For each feed item (key `c<conversation id>` or `b<block id>`): its conversation, whether that's
 *  approved, and whether the item opens or closes the conversation's run in the loaded feed.
 *  Approval comes from the conversation's header when it's loaded (always current), else from the block. */
export function chunks(items: FeedItem[]): Map<string, ChunkInfo> {
  const approved = new Map<number, boolean>()
  for (const it of items) if (it.type === 'conversation_start') approved.set(it.conversation.id, it.conversation.approved)
  const convOf = (it: FeedItem | undefined) => (!it ? undefined : it.type === 'block' ? it.conversation_id : it.conversation.id)
  const out = new Map<string, ChunkInfo>()
  items.forEach((it, i) => {
    const id = convOf(it) ?? null
    const prev = items[i - 1]
    const next = items[i + 1]
    out.set(it.type === 'block' ? `b${it.id}` : `c${it.conversation.id}`, {
      id,
      approved: id != null && (approved.get(id) ?? (it.type === 'block' && !!it.conversation_approved)),
      top: it.type === 'conversation_start' || convOf(prev) !== id,
      // a header is never the end of its conversation's run, even if nothing below it is loaded yet
      bottom: it.type === 'block' && (next?.type !== 'block' || convOf(next) !== id)
    })
  })
  return out
}

/** Marks the rows of a conversation as one rounded chunk, headers included:
 *  - approved: a faint green tint, so approved stretches stand out while scrolling
 *  - the one the header acts on (`currentId`): a grey bar down the left edge
 *  - an Approve button for it is hovered (`approvingId`): a green bar and a stronger tint
 *  Marked rows reach 12px past the text on each side (-mx-3 px-3) so the tint doesn't hug it.
 *  `top`/`bottom` default to the chunk's own edges; Raw passes its per-line edges. */
export function approveMark(
  c: ChunkInfo | undefined,
  currentId: number | undefined,
  approvingId: number | null,
  edges: { top: boolean; bottom: boolean } = c ?? { top: false, bottom: false }
) {
  if (!c || c.id == null) return null
  const approving = c.id === approvingId
  const bar = approving || c.id === currentId
  const tint = approving || c.approved
  if (!tint && !bar) return null
  return cx(
    '-mx-3 px-3',
    approving ? 'bg-[#6CCB98]/[0.07]' : tint && 'bg-[#6CCB98]/[0.035]',
    tint && edges.top && 'rounded-t-lg',
    tint && edges.bottom && 'rounded-b-lg',
    bar && "relative before:absolute before:inset-y-0 before:-left-2 before:w-0.5 before:rounded-full before:content-['']",
    bar && (approving ? 'before:bg-[#6CCB98]' : 'before:bg-[#4a4d55]')
  )
}

/** Hover/focus handlers that tell the feed which conversation an Approve button would approve. */
export const approveHover = (id: number, set: (id: number | null) => void) => ({
  onMouseEnter: () => set(id),
  onMouseLeave: () => set(null),
  onFocus: () => set(id),
  onBlur: () => set(null)
})
