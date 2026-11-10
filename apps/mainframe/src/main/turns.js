// Recent turns for the Pipeline tab, posted whole by apps/engine/utils/trace.py. Memory only.
import { onEngineChange } from './engine.js'

const KEEP = 60
const turns = new Map() // id -> turn, in arrival order

export function recordTurn(turn) {
  if (!turn || typeof turn.id !== 'string' || !turn.id) throw new Error('a turn needs an id')
  turns.delete(turn.id) // re-insert so it goes last
  turns.set(turn.id, { ...turn, received_at: Date.now() })
  while (turns.size > KEEP) turns.delete(turns.keys().next().value)
}

/** Newest first. */
export function listTurns(count = 30) {
  return [...turns.values()].sort((a, b) => b.started_at - a.started_at).slice(0, count)
}

// Unfinished turns die with the engine
onEngineChange(({ state }) => {
  if (state !== 'stopped') return
  for (const t of turns.values()) {
    if (t.status === 'running' || t.status === 'hearing') {
      t.status = 'lost'
      t.ended_at = Date.now()
      t.end_note = 'the engine stopped'
      for (const s of Object.values(t.stages ?? {})) {
        if (s.status === 'running') s.status = 'skipped'
      }
    }
  }
})
