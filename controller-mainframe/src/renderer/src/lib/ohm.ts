// The app's own backend (main process): the engine, modules, settings, and the NAS through it.
import { useEffect, useState } from 'react'

export type EngineState = 'stopped' | 'starting' | 'running' | 'stopping'

export type EngineStatus = {
  state: EngineState
  pid: number | null
  startedAt: string | null
  external: boolean
  lastBeat: number | null
}

export type Module = {
  name: string
  description: string
  required: boolean
  enabled: boolean
  ok: boolean | null // null: off, or the engine isn't running
  detail: string
  since: string | null
  restarting: boolean
}

export type Day = 'up' | 'restarted' | 'down' | 'off' | 'none'

export type Health = {
  days: number
  engine: EngineStatus & {
    uptime: number | null
    restarts: number
    crashes: number
    errors: number
    days: Day[]
  }
  modules: Record<
    string,
    { uptime: number | null; restarts: number; errors: number; since: string | null; days: Day[] }
  >
}

export type Character = {
  id: string
  name: string
  created: string
  status: 'Active' | 'Inactive'
  description: string
  picture: string
  icon: string
  model: { base_model_id?: string | null; adapter_id?: string | null; ohm_path?: string | null }
}

export const nas = {
  get: <T = any>(path: string): Promise<T> => window.api.nas('GET', path),
  post: <T = any>(path: string, body: any = {}): Promise<T> => window.api.nas('POST', path, body),
  patch: <T = any>(path: string, body: any = {}): Promise<T> => window.api.nas('PATCH', path, body),
  del: <T = any>(path: string): Promise<T> => window.api.nas('DELETE', path)
}

/** Electron prefixes errors from the main process; keep just the sentence. */
export const message = (e: unknown) =>
  String((e as Error)?.message || e).replace(
    /^Error invoking remote method '[^']+': (Error: )?/,
    ''
  )

/** The engine's status, kept current by the main process. */
export function useEngine() {
  const [status, setStatus] = useState<EngineStatus | null>(null)
  useEffect(() => {
    window.api.engineStatus().then(setStatus)
    return window.api.onEngineChange(setStatus)
  }, [])
  return status
}

/** Run `load` now and every `ms` after, while the component is mounted. */
export function usePoll<T>(load: () => Promise<T>, ms: number, deps: any[] = []) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let live = true
    const run = () =>
      load()
        .then((d) => live && (setData(d), setError(null)))
        .catch((e) => live && setError(message(e)))
    run()
    const timer = setInterval(run, ms)
    return () => {
      live = false
      clearInterval(timer)
    }
  }, deps)
  return { data, error }
}

/** "3d 4h", "12m", "40s" */
export function since(start: string | number | null | undefined, now = Date.now()) {
  if (!start) return '—'
  const s = Math.max(0, Math.round((now - +new Date(start)) / 1000))
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  if (d) return `${d}d ${h}h`
  if (h) return `${h}h ${m}m`
  if (m) return `${m}m`
  return `${s}s`
}

export const pct = (x: number | null | undefined) =>
  x == null ? '—' : x >= 0.9995 ? '100%' : `${(x * 100).toFixed(1)}%`

const time = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' })
const day = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' })
export const fmtTime = (t: string | number | Date) => time.format(new Date(t))

export function fmtDay(t: string | number | Date) {
  const d = new Date(t)
  const today = new Date()
  const yesterday = new Date(Date.now() - 86400000)
  if (d.toDateString() === today.toDateString()) return 'Today'
  if (d.toDateString() === yesterday.toDateString()) return 'Yesterday'
  return day.format(d)
}

// One color per person, the same palette as the Curator
const PALETTE = [
  '#5EC8E5',
  '#A78BFA',
  '#E8CF4A',
  '#F47A9B',
  '#F59E5B',
  '#6CCB98',
  '#9AA5B8',
  '#F28B82'
]
export function personColor(id: string | null | undefined) {
  let h = 0
  for (const c of String(id ?? '')) h = (h * 31 + c.charCodeAt(0)) >>> 0
  return PALETTE[h % PALETTE.length]
}
export const OHM_COLOR = '#E3967F'

// Module folder names → what the UI calls them
export const MODULE_NAMES: Record<string, string> = {
  discord: 'Discord',
  memory: 'Memory',
  stt: 'Speech to text',
  tts: 'Text to speech',
  vision: 'Vision'
}
export const moduleName = (name: string) => MODULE_NAMES[name] ?? name

/** The character deck, and the one in play (the first card when none is). */
export function useCharacters() {
  const [list, setList] = useState<Character[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const reload = () =>
    window.api
      .getCharacters()
      .then((l) => (setList(l), setError(null)))
      .catch((e) => setError(message(e)))
  useEffect(() => {
    reload()
  }, [])
  const active = list?.find((c) => c.status === 'Active') ?? list?.[0] ?? null
  return { list, setList, active, error, reload }
}
