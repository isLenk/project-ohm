import { useEffect, useRef, useState } from 'react'
import { LuSlidersHorizontal, LuX } from 'react-icons/lu'
import type { Author, ConversationStats } from './api'
import { Avatar } from './Avatar'
import { cx } from './util'

// Hides conversations from the sidebar list. Nothing is changed in the project; training only ever uses approved ones.
export type ConvFilters = {
  minMessages: number | null
  maxMessages: number | null
  minPeople: number | null
  maxPeople: number | null
  minMinutes: number | null
  maxMinutes: number | null
  maxTopShare: number | null // percent, 1-100
  person: string | null
  from: string | null // yyyy-mm-dd, local
  to: string | null
  unattached: 'any' | 'some' | 'none'
}

export const NO_FILTERS: ConvFilters = {
  minMessages: null, maxMessages: null, minPeople: null, maxPeople: null, minMinutes: null, maxMinutes: null,
  maxTopShare: null, person: null, from: null, to: null, unattached: 'any'
}

const PANEL_WIDTH = 276

const key = (path: string) => `curator.filters:${path}`

export function loadFilters(path: string | undefined): ConvFilters {
  if (!path) return NO_FILTERS
  try {
    return { ...NO_FILTERS, ...JSON.parse(localStorage.getItem(key(path)) || '{}') }
  } catch {
    return NO_FILTERS
  }
}

export function saveFilters(path: string | undefined, f: ConvFilters) {
  if (!path) return
  if (activeCount(f)) localStorage.setItem(key(path), JSON.stringify(f))
  else localStorage.removeItem(key(path))
}

export function activeCount(f: ConvFilters) {
  return (Object.keys(NO_FILTERS) as (keyof ConvFilters)[]).filter((k) => f[k] !== NO_FILTERS[k]).length
}

const dayStart = (d: string) => new Date(`${d}T00:00:00`).getTime()

export function applyFilters(list: ConversationStats[], f: ConvFilters): ConversationStats[] {
  if (!activeCount(f)) return list
  const from = f.from ? dayStart(f.from) : null
  const to = f.to ? dayStart(f.to) + 86_400_000 : null
  return list.filter((c) => {
    const minutes = (c.last_ts - c.first_ts) / 60_000
    const top = c.skew[0]?.share ?? 0
    return (
      (f.minMessages == null || c.messages >= f.minMessages) &&
      (f.maxMessages == null || c.messages <= f.maxMessages) &&
      (f.minPeople == null || c.people >= f.minPeople) &&
      (f.maxPeople == null || c.people <= f.maxPeople) &&
      (f.minMinutes == null || minutes >= f.minMinutes) &&
      (f.maxMinutes == null || minutes <= f.maxMinutes) &&
      (f.maxTopShare == null || top * 100 <= f.maxTopShare) &&
      (!f.person || c.skew.some((s) => s.author_id === f.person)) &&
      (from == null || c.last_ts >= from) &&
      (to == null || c.first_ts < to) &&
      (f.unattached === 'any' || (f.unattached === 'some' ? c.unattached > 0 : c.unattached === 0))
    )
  })
}

function Num({ value, onChange, placeholder, max }: { value: number | null; onChange: (v: number | null) => void; placeholder: string; max?: number }) {
  return (
    <input
      type="number"
      min={0}
      max={max}
      inputMode="numeric"
      value={value ?? ''}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value === '' ? null : Math.max(0, Number(e.target.value)))}
      className="h-8 w-full min-w-0 rounded border border-line bg-page px-2 text-[13px] text-gray-100 placeholder:text-muted focus:border-gray-400 focus:outline-none"
    />
  )
}

function Range({ label, lo, hi, set, unit }: { label: string; lo: number | null; hi: number | null; set: (lo: number | null, hi: number | null) => void; unit?: string }) {
  return (
    <label className="grid grid-cols-[88px_1fr_10px_1fr] items-center gap-1.5 text-[12.5px] text-gray-300">
      <span>
        {label}
        {unit && <span className="text-muted"> ({unit})</span>}
      </span>
      <Num value={lo} onChange={(v) => set(v, hi)} placeholder="min" />
      <span className="text-center text-muted">–</span>
      <Num value={hi} onChange={(v) => set(lo, v)} placeholder="max" />
    </label>
  )
}

export function FilterButton({ filters, onChange, authors }: { filters: ConvFilters; onChange: (f: ConvFilters) => void; authors: Record<string, Author> }) {
  const [open, setOpen] = useState(false)
  const [place, setPlace] = useState<{ left: number; top: number; maxHeight: number } | null>(null)
  const box = useRef<HTMLDivElement>(null)
  const btn = useRef<HTMLButtonElement>(null)

  // Fixed position, kept inside the Curator sidebar: the app's own nav sits to the left of it,
  // and the page clips anything that spills past its edge.
  const toggle = () => {
    if (open) return setOpen(false)
    const b = btn.current!.getBoundingClientRect()
    const side = btn.current!.closest('nav')?.getBoundingClientRect()
    const minLeft = (side?.left ?? 0) + 8
    const maxRight = Math.min(side?.right ?? innerWidth, innerWidth) - 8
    const left = Math.max(minLeft, Math.min(b.right + 12 - PANEL_WIDTH, maxRight - PANEL_WIDTH))
    const top = b.bottom + 6
    setPlace({ left, top, maxHeight: innerHeight - top - 12 })
    setOpen(true)
  }
  const n = activeCount(filters)
  const set = (patch: Partial<ConvFilters>) => onChange({ ...filters, ...patch })

  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false)
    }
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    const shut = () => setOpen(false) // the panel is placed once, so it would drift on resize
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', esc)
    window.addEventListener('resize', shut)
    return () => {
      document.removeEventListener('mousedown', close)
      document.removeEventListener('keydown', esc)
      window.removeEventListener('resize', shut)
    }
  }, [open])

  const people = Object.values(authors)
    .filter((a) => !a.is_bot)
    .sort((a, b) => (a.display_name || a.name).localeCompare(b.display_name || b.name))

  return (
    <div ref={box} className="relative ml-auto self-center">
      <button
        ref={btn}
        onClick={toggle}
        aria-expanded={open}
        title="Filter the list"
        className={cx('flex h-[26px] items-center gap-1.5 rounded px-1.5 text-[12.5px] hover:bg-raised', n ? 'text-gray-100' : 'text-muted')}
      >
        <LuSlidersHorizontal className="size-3.5" />
        {n > 0 && <span className="rounded bg-gray-100 px-1 font-mono text-[10.5px] leading-4 text-ink">{n}</span>}
      </button>
      {open && place && (
        <div
          role="dialog"
          aria-label="Filter conversations"
          style={{ left: place.left, top: place.top, maxHeight: place.maxHeight, width: PANEL_WIDTH }}
          className="fixed z-50 flex flex-col gap-2.5 overflow-auto rounded-lg border border-line bg-ink p-3.5 shadow-2xl"
        >
          <div className="flex items-center">
            <span className="flex-1 text-[13px] font-semibold">Show conversations with</span>
            <button onClick={() => setOpen(false)} aria-label="Close" className="grid size-6 place-items-center rounded text-muted hover:bg-raised hover:text-gray-100">
              <LuX className="size-3.5" />
            </button>
          </div>
          <Range label="Messages" lo={filters.minMessages} hi={filters.maxMessages} set={(a, b) => set({ minMessages: a, maxMessages: b })} />
          <Range label="People" lo={filters.minPeople} hi={filters.maxPeople} set={(a, b) => set({ minPeople: a, maxPeople: b })} />
          <Range label="Length" unit="min" lo={filters.minMinutes} hi={filters.maxMinutes} set={(a, b) => set({ minMinutes: a, maxMinutes: b })} />
          <label className="grid grid-cols-[88px_1fr] items-center gap-1.5 text-[12.5px] text-gray-300">
            <span title="Hide conversations one person dominates">Top author</span>
            <span className="flex items-center gap-1.5">
              <span className="text-muted">at most</span>
              <Num value={filters.maxTopShare} onChange={(v) => set({ maxTopShare: v == null ? null : Math.min(100, v) })} placeholder="%" max={100} />
              <span className="text-muted">%</span>
            </span>
          </label>
          <label className="grid grid-cols-[88px_1fr] items-center gap-1.5 text-[12.5px] text-gray-300">
            <span>Includes</span>
            <span className="flex min-w-0 items-center gap-1.5">
              {filters.person && <Avatar who={authors[filters.person]} size={18} />}
              <select
                value={filters.person ?? ''}
                onChange={(e) => set({ person: e.target.value || null })}
                className="h-8 w-full min-w-0 rounded border border-line bg-page px-1.5 text-[13px] text-gray-100 focus:border-gray-400 focus:outline-none"
              >
                <option value="">Anyone</option>
                {people.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.display_name || a.name}
                  </option>
                ))}
              </select>
            </span>
          </label>
          <label className="grid grid-cols-[88px_1fr_10px_1fr] items-center gap-1.5 text-[12.5px] text-gray-300">
            <span>Dates</span>
            {(['from', 'to'] as const).map((k, i) => (
              <span key={k} className={cx('contents')}>
                {i === 1 && <span className="text-center text-muted">–</span>}
                <input
                  type="date"
                  value={filters[k] ?? ''}
                  onChange={(e) => set({ [k]: e.target.value || null })}
                  className="h-8 w-full min-w-0 rounded border border-line bg-page px-1 text-[12px] text-gray-100 [color-scheme:dark] focus:border-gray-400 focus:outline-none"
                />
              </span>
            ))}
          </label>
          <div className="grid grid-cols-[88px_1fr] items-center gap-1.5 text-[12.5px] text-gray-300">
            <span>Unattached</span>
            <span className="flex overflow-hidden rounded border border-line">
              {(
                [
                  ['any', 'Any'],
                  ['some', 'Has some'],
                  ['none', 'None']
                ] as const
              ).map(([v, label]) => (
                <button
                  key={v}
                  onClick={() => set({ unattached: v })}
                  className={cx('h-7 flex-1 text-[12px]', filters.unattached === v ? 'bg-gray-100 font-semibold text-ink' : 'text-gray-300 hover:bg-raised')}
                >
                  {label}
                </button>
              ))}
            </span>
          </div>
          <div className="flex items-center pt-1">
            <span className="flex-1 text-[11.5px] text-muted">Only hides them here. Nothing is deleted.</span>
            <button onClick={() => onChange(NO_FILTERS)} disabled={!n} className="h-7 rounded border border-line px-2.5 text-[12px] hover:bg-raised disabled:opacity-40">
              Clear
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
