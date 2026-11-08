import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { LuBookOpen, LuCircleHelp, LuEyeOff, LuLink } from 'react-icons/lu'
import { findTerm, saveTerm, useTerms, withSuggestion, type Term, type TermStatus } from './terms'
import { cx } from './util'
import { Spinner } from '../components/Loader'

export const TERM_STATUS: Record<TermStatus, { label: string; icon: typeof LuCircleHelp; color: string }> = {
  unknown: { label: 'Not known yet', icon: LuCircleHelp, color: '#E0B25C' },
  defined: { label: 'Defined', icon: LuBookOpen, color: '#6CCB98' },
  linked: { label: 'Typo or same meaning', icon: LuLink, color: '#5EC8E5' },
  ignored: { label: 'Normal word', icon: LuEyeOff, color: '#8b8d97' }
}

export function TermIcon({ status, className = 'size-4' }: { status: TermStatus; className?: string }) {
  const s = TERM_STATUS[status]
  const Icon = s.icon
  return (
    <span title={s.label} aria-label={s.label} role="img" className="inline-flex shrink-0" style={{ color: s.color }}>
      <Icon className={className} />
    </span>
  )
}

type Mode = 'define' | 'typo' | 'same'

/** Define a term, or say it's a typo of / means the same as another word. */
export function TermEditor({ term, initial, onSaved, onCancel, onError, autoFocus = true }: {
  term: string
  initial: Term | null
  onSaved: (t: Term) => void
  onCancel: () => void
  onError: (e: unknown) => void
  autoFocus?: boolean
}) {
  const { terms } = useTerms()
  const [mode, setMode] = useState<Mode>(initial?.related_term ? (initial.relation === 'typo' ? 'typo' : 'same') : 'define')
  const [definition, setDefinition] = useState(initial?.definition ?? '')
  const [related, setRelated] = useState(initial?.related_term ?? '')
  const [saving, setSaving] = useState(false)
  const listId = useId()

  const ready = mode === 'define' ? !!definition.trim() : !!related.trim()
  const save = async () => {
    if (!ready || saving) return
    setSaving(true)
    try {
      // Saving one kind clears the other, so a term is either defined or linked.
      const t = await saveTerm(
        mode === 'define'
          ? { term, definition: definition.trim(), related_term: '' }
          : { term, relation: mode, related_term: related.trim(), definition: '' }
      )
      onSaved(t)
    } catch (e) {
      onError(e)
    } finally {
      setSaving(false)
    }
  }

  return (
    <form
      className="flex flex-col gap-2.5"
      onSubmit={(e) => {
        e.preventDefault()
        save()
      }}
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          e.stopPropagation()
          onCancel()
        }
      }}
    >
      <div role="tablist" className="flex gap-1 text-xs">
        {(
          [
            ['define', 'Definition'],
            ['typo', 'Typo of…'],
            ['same', 'Same meaning as…']
          ] as const
        ).map(([m, label]) => (
          <button
            key={m}
            type="button"
            role="tab"
            aria-selected={mode === m}
            onClick={() => setMode(m)}
            className={cx('rounded px-2 py-1', mode === m ? 'bg-gray-100 text-ink' : 'border border-line text-muted hover:text-gray-200')}
          >
            {label}
          </button>
        ))}
      </div>
      {mode === 'define' ? (
        <textarea
          autoFocus={autoFocus}
          value={definition}
          onChange={(e) => setDefinition(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              save()
            }
          }}
          rows={2}
          placeholder={`What does “${term}” mean?`}
          className="resize-none rounded-md border border-line bg-transparent px-2.5 py-1.5 text-[13px] outline-none focus:border-gray-400"
        />
      ) : (
        <>
          <input
            autoFocus={autoFocus}
            value={related}
            onChange={(e) => setRelated(e.target.value)}
            list={listId}
            placeholder={mode === 'typo' ? `“${term}” is a typo of…` : `“${term}” means the same as…`}
            className="h-8 rounded-md border border-line bg-transparent px-2.5 text-[13px] outline-none focus:border-gray-400"
          />
          <datalist id={listId}>
            {terms
              .filter((t) => t.term !== term && t.status !== 'ignored')
              .slice(0, 300)
              .map((t) => (
                <option key={t.id} value={t.term} />
              ))}
          </datalist>
        </>
      )}
      <div className="flex justify-end gap-2">
        <button type="button" onClick={onCancel} className="rounded-md px-2.5 py-1 text-xs text-muted hover:text-gray-200">
          Cancel
        </button>
        <button
          disabled={!ready || saving}
          className="flex items-center gap-1.5 rounded-md bg-gray-100 px-2.5 py-1 text-xs font-semibold text-ink hover:bg-white disabled:opacity-50"
        >
          {saving && <Spinner className="size-3" />}
          Save
        </button>
      </div>
    </form>
  )
}

// ── Ctrl+click a word ────────────────────────────────────────────────────────
const WORD_CHAR = /[\p{L}\p{N}'’_-]/u

function inside(rect: DOMRect, x: number, y: number) {
  return x >= rect.left - 1 && x <= rect.right + 1 && y >= rect.top - 1 && y <= rect.bottom + 1
}

/** The word under the pointer, or the selected text if the click lands on a selection (for phrases).
 *  Selects it, so it stays highlighted while the picker is open. */
function pick(x: number, y: number): { text: string; rect: DOMRect } | null {
  const sel = window.getSelection()
  if (sel && !sel.isCollapsed && sel.rangeCount) {
    const r = sel.getRangeAt(0)
    const text = sel.toString().trim().replace(/\s+/g, ' ')
    if (text && text.length <= 80 && [...r.getClientRects()].some((rect) => inside(rect, x, y))) {
      return { text, rect: r.getBoundingClientRect() }
    }
  }
  const caret = document.caretRangeFromPoint?.(x, y)
  if (!caret || caret.startContainer.nodeType !== Node.TEXT_NODE) return null
  const node = caret.startContainer as Text
  const data = node.data
  let start = caret.startOffset
  let end = caret.startOffset
  while (start > 0 && WORD_CHAR.test(data[start - 1])) start--
  while (end < data.length && WORD_CHAR.test(data[end])) end++
  while (start < end && /['’_-]/.test(data[start])) start++
  while (end > start && /['’_-]/.test(data[end - 1])) end--
  if (end <= start) return null
  const range = document.createRange()
  range.setStart(node, start)
  range.setEnd(node, end)
  const rect = range.getBoundingClientRect()
  // caretRangeFromPoint snaps to the nearest text, even from blank space beside it.
  if (!inside(rect, x, y)) return null
  sel?.removeAllRanges()
  sel?.addRange(range)
  return { text: data.slice(start, end).replace(/’/g, "'"), rect }
}

/** Ctrl+click (Cmd+click on a Mac) any word inside `[data-terms]` to define it. Mounted once by CuratorPage. */
export function TermPicker({ onDirty, onError }: { onDirty: () => void; onError: (e: unknown) => void }) {
  const [open, setOpen] = useState<{ text: string; rect: DOMRect } | null>(null)
  const [saved, setSaved] = useState<string | null>(null)
  const box = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null)
  useTerms()

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.button !== 0 || e.shiftKey || e.altKey) return
      const target = e.target as HTMLElement | null
      if (!target?.closest('[data-terms]') || target.closest('input, textarea, select, [contenteditable="true"]')) return
      if (box.current?.contains(target)) return
      const picked = pick(e.clientX, e.clientY)
      if (!picked) return
      // Capture phase on document runs before React's handlers, so the row underneath isn't clicked too.
      e.preventDefault()
      e.stopPropagation()
      setSaved(null)
      setPos(null)
      setOpen(picked)
    }
    // Without this, Ctrl+mousedown clears a highlighted phrase before the click can read it,
    // and starts a drag or focus on links and buttons.
    const onDown = (e: MouseEvent) => {
      const target = e.target as HTMLElement | null
      if ((e.ctrlKey || e.metaKey) && e.button === 0 && target?.closest('[data-terms]') && !target.closest('input, textarea, select')) {
        e.preventDefault()
      }
    }
    document.addEventListener('click', onClick, true)
    document.addEventListener('mousedown', onDown, true)
    return () => {
      document.removeEventListener('click', onClick, true)
      document.removeEventListener('mousedown', onDown, true)
    }
  }, [])

  const close = () => {
    setOpen(null)
    window.getSelection()?.removeAllRanges()
  }

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node) && !(e.ctrlKey || e.metaKey)) close()
    }
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && close()
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  // Below the word, or above it when there's no room; kept on screen.
  useLayoutEffect(() => {
    if (!open || !box.current) return
    const h = box.current.offsetHeight
    const w = box.current.offsetWidth
    const below = open.rect.bottom + 8
    const top = below + h > window.innerHeight - 8 ? Math.max(8, open.rect.top - 8 - h) : below
    const left = Math.min(Math.max(8, open.rect.left), window.innerWidth - w - 8)
    setPos({ left, top })
  }, [open, saved])

  if (!open) return null
  const existing = findTerm(open.text)
  return createPortal(
    <div
      ref={box}
      role="dialog"
      aria-label={`Define ${open.text}`}
      className="fixed z-[80] w-[340px] rounded-lg border border-[#3a3b42] bg-[#1d1e22] p-3.5 shadow-2xl"
      style={pos ?? { left: -9999, top: -9999 }}
    >
      <div className="mb-2.5 flex items-center gap-2">
        {existing && <TermIcon status={existing.status} />}
        <span className="min-w-0 flex-1 truncate text-[14px] font-semibold text-gray-100">{open.text}</span>
        {existing && existing.uses > 0 && <span className="font-mono text-[11px] text-muted">used {existing.uses}×</span>}
      </div>
      {saved ? (
        <p className="text-[13px] text-[#6CCB98]">{saved}</p>
      ) : (
        <TermEditor
          key={open.text}
          term={open.text}
          initial={existing && withSuggestion(existing)}
          onCancel={close}
          onError={onError}
          onSaved={(t) => {
            onDirty()
            setSaved(
              t.related_term
                ? `Saved: “${t.term}” ${t.relation === 'typo' ? 'is a typo of' : 'means the same as'} “${t.related_term}”.`
                : `Saved. Ohm will read “${t.term}” as: ${t.definition}`
            )
            window.setTimeout(close, 1400)
          }}
        />
      )}
    </div>,
    document.body
  )
}
