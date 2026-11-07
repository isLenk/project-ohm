/* Uptime, restarts and errors for the engine and each module, worked out from events.db.

   The engine is up between a 'start' and the next 'stop' or 'crash'. A module is up between an
   'up' and the next 'down' or 'off', and only counts while the engine is up. Time the app wasn't
   open isn't known, so it isn't counted either way.
*/
import { engineStatus } from './engine.js'
import { listModules } from './api.js'
import { eventsDB, logsDB, find } from './db.js'

const DAY = 24 * 60 * 60 * 1000
const STRIP_DAYS = 30

/** [[from, to]] spans where the state is on. `isOn` maps an event to true/false, or undefined to ignore it. */
function spans(events, isOn, end) {
  const out = []
  let on = false
  let since = 0
  for (const e of events) {
    const next = isOn(e.event)
    if (next === undefined) continue
    const t = +e.at
    if (on && !next) out.push([since, t])
    if (!on && next) since = t
    on = next
  }
  if (on) out.push([since, end])
  return out
}

const overlap = (list, a, b) =>
  list.reduce((sum, [from, to]) => sum + Math.max(0, Math.min(to, b) - Math.max(from, a)), 0)

const count = (events, names, a, b) =>
  events.filter((e) => names.includes(e.event) && +e.at >= a && +e.at < b).length

const startOfDay = (t) => {
  const d = new Date(t)
  d.setHours(0, 0, 0, 0)
  return +d
}

/** up | restarted | down | off | none for each of the last 30 days, oldest first. */
function strip(engineUp, up, events, troubles, off, now) {
  const days = []
  for (let i = STRIP_DAYS - 1; i >= 0; i--) {
    const a = startOfDay(now) - i * DAY
    const b = Math.min(a + DAY, now)
    const ran = overlap(engineUp, a, b)
    const was = overlap(up, a, b)
    if (!ran) days.push(off && !was ? 'off' : 'none')
    else if (was / ran < 0.9) days.push('down')
    else if (count(events, troubles, a, b)) days.push('restarted')
    else days.push('up')
  }
  return days
}

export async function health(days = 7) {
  const now = Date.now()
  const from = now - days * DAY
  const since = Math.min(from, startOfDay(now) - STRIP_DAYS * DAY)
  const events = await find(eventsDB, { at: { $gte: new Date(since - DAY) } })
  const errors = await find(logsDB, { log_type: 'error', timestamp: { $gte: new Date(from) } }, {})

  const engineEvents = events.filter((e) => e.kind === 'engine')
  const engineUp = spans(engineEvents, (e) => ({ start: true, stop: false, crash: false })[e], now)
  const observed = now - Math.max(from, +(events[0]?.at ?? now))
  const ran = overlap(engineUp, from, now)

  const status = engineStatus()
  const engine = {
    ...status,
    uptime: observed > 0 ? ran / observed : null,
    restarts: count(engineEvents, ['restart'], from, now),
    crashes: count(engineEvents, ['crash'], from, now),
    errors: errors.length,
    days: strip(engineUp, engineUp, engineEvents, ['crash', 'restart'], false, now)
  }

  const modules = {}
  for (const m of listModules()) {
    const mine = events.filter((e) => e.kind === 'module' && e.name === m.name)
    const up = spans(mine, (e) => ({ up: true, down: false, off: false })[e], now)
    const upTime = overlap(up, from, now)
    modules[m.name] = {
      uptime: ran > 0 && (m.enabled || upTime) ? Math.min(1, upTime / ran) : null,
      restarts: count(mine, ['restart'], from, now),
      errors: count(mine, ['down'], from, now),
      since: m.ok ? m.since : null,
      days: strip(engineUp, up, mine, ['restart', 'down'], !m.enabled, now)
    }
  }
  return { days, engine, modules }
}
