// General terminology: one shared copy of the project's terms, so the Ctrl+click picker and the
// terminology tab stay in step. See Documentation/Curator/terminology.md.
import { useEffect, useSyncExternalStore } from 'react'
import { del, get, patch, post } from './api'

export type TermStatus = 'unknown' | 'defined' | 'linked' | 'ignored'

/** The local model's guess at what an unknown term means. Nothing changes until it's approved. */
export type Suggestion =
  | { kind: 'definition'; definition: string; label?: 'slang' | 'name' | 'thing' | null; meaning?: string }
  | { kind: 'typo' | 'same'; related_term: string; meaning?: string }
  | { kind: 'normal'; meaning?: string }
  | { kind: 'unsure'; dismissed?: boolean }

export type Term = {
  id: number
  term: string
  definition: string | null
  relation: 'typo' | 'same' | null
  related_term: string | null
  related_definition: string | null
  ignored: boolean
  origin: 'detected' | 'manual'
  uses: number
  people: number
  status: TermStatus
  examples: { id: string; ts: number; content_text: string; who: string }[]
  suggestion?: Suggestion | null
}

export type TermCounts = Record<TermStatus, number>

/** How the last push of the defined terms to memory_service (where Ohm reads them) went. null: not tried yet. */
export type TermsShared = { ok: boolean; terms: number; at: number | null; error: string | null } | null

type State = { terms: Term[]; counts: TermCounts; shared: TermsShared; loaded: boolean }

let state: State = { terms: [], counts: { unknown: 0, defined: 0, linked: 0, ignored: 0 }, shared: null, loaded: false }
const subs = new Set<() => void>()
const set = (s: State) => {
  state = s
  subs.forEach((f) => f())
}

/** Load again once the background push to memory_service has had time to finish, so `shared` is current. */
export function reloadAfterShare() {
  setTimeout(() => loadTerms().catch(() => {}), 2500)
}

export async function loadTerms() {
  const r = await get<{ terms: Term[]; counts: TermCounts; shared?: TermsShared }>('/terms')
  set({ terms: r.terms, counts: r.counts, shared: r.shared ?? null, loaded: true })
}

/** Send the defined terms to memory_service now. They also go on their own after every change. */
export async function shareTerms() {
  await post('/terms/share')
  await loadTerms()
}

/** Add a term or change it (by its text). What the Ctrl+click picker calls. */
export async function saveTerm(body: { term: string; definition?: string; relation?: 'typo' | 'same'; related_term?: string }) {
  const t = await post<Term>('/terms', body)
  await loadTerms()
  reloadAfterShare()
  return t
}

export async function updateTerm(id: number, body: Partial<Pick<Term, 'definition' | 'relation' | 'related_term' | 'ignored'>>) {
  const t = await patch<Term>(`/terms/${id}`, body)
  await loadTerms()
  reloadAfterShare()
  return t
}

/** A suggestion worth showing: there is one, and the model wasn't unsure. */
export const hasSuggestion = (t: Term) => t.status === 'unknown' && !!t.suggestion && t.suggestion.kind !== 'unsure'

/** A term as the editor should start it: the suggestion filled in, for fixing before saving. */
export function withSuggestion(t: Term): Term {
  const s = t.suggestion
  if (!s || t.status !== 'unknown') return t
  if (s.kind === 'definition') return { ...t, definition: s.definition }
  if (s.kind === 'typo' || s.kind === 'same') return { ...t, relation: s.kind, related_term: s.related_term }
  return t
}

export async function acceptSuggestion(id: number) {
  const t = await post<Term>(`/terms/${id}/accept`)
  await loadTerms()
  reloadAfterShare()
  return t
}

export async function acceptAllSuggestions() {
  const r = await post<{ accepted: number }>('/terms/accept-all')
  await loadTerms()
  reloadAfterShare()
  return r.accepted
}

export async function dismissSuggestion(id: number) {
  await del(`/terms/${id}/suggestion`)
  await loadTerms()
}

export function useTerms(reload = false) {
  const s = useSyncExternalStore(
    (f) => {
      subs.add(f)
      return () => subs.delete(f)
    },
    () => state
  )
  useEffect(() => {
    // A different project may be open since the last load, so the tab always fetches.
    if (reload || !state.loaded) loadTerms().catch(() => {})
  }, [reload])
  return s
}

export const findTerm = (text: string) => {
  const key = text.trim().toLowerCase().replace(/\s+/g, ' ')
  return state.terms.find((t) => t.term === key) ?? null
}
