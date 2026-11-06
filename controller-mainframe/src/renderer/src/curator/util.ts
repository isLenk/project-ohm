import type { Author } from './api'

// One color per person, everywhere: dot, name tick, arrow. Indexed by authors.color_index.
export const PALETTE = ['#5EC8E5', '#A78BFA', '#E8CF4A', '#F47A9B', '#F59E5B', '#6CCB98', '#9AA5B8', '#F28B82']

export const color = (index: number | undefined) => PALETTE[(index ?? 0) % PALETTE.length]

// Each person's arrows bulge out to their own lane in the gutter so curves don't overlap.
export const GUTTER = 160
export const lane = (index: number | undefined) => 40 + ((index ?? 0) % 6) * 20

export const nameOf = (a: Author | undefined, fallback = 'Unknown') =>
  a ? a.display_name || a.name : fallback

const time = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' })
const day = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' })

export const fmtTime = (ms: number) => time.format(new Date(ms))
export const fmtDay = (ms: number) => day.format(new Date(ms))
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

export function cx(...parts: (string | false | null | undefined)[]) {
  return parts.filter(Boolean).join(' ')
}
