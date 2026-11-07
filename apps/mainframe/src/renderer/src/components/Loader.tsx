import { useCallback, useRef, useState, type ReactNode } from 'react'
import { LuLoader } from 'react-icons/lu'

/** The app's spinner. */
export const Spinner = ({ className = 'size-4' }: { className?: string }) => (
  <LuLoader aria-hidden className={`${className} shrink-0 animate-spin`} />
)

/** Spinner and a line of text, for a list or panel that's still loading. */
export const Loading = ({ label, className = '' }: { label: string; className?: string }) => (
  <div
    role="status"
    className={`flex items-center justify-center gap-2.5 py-10 text-sm text-muted ${className}`}
  >
    <Spinner /> {label}
  </div>
)

/** Covers the window (or, with `area`, the nearest `relative` parent) while something slow runs.
 *  It takes clicks at once, so nothing gets clicked twice, but only shows after a beat
 *  (.loader-in in main.css), so quick jobs don't flash. */
export function LoadingOverlay({ label, area = false }: { label: ReactNode; area?: boolean }) {
  return (
    <div
      role="status"
      aria-live="polite"
      className={`${area ? 'absolute z-30 bg-page/60' : 'fixed z-[60] bg-black/45'} loader-in inset-0 grid place-items-center`}
    >
      <div className="flex max-w-[420px] items-center gap-3 rounded-lg border border-[#3a3b42] bg-[#1d1e22] px-5 py-3.5 text-sm text-gray-200 shadow-xl">
        <Spinner />
        <span className="truncate">{label}</span>
      </div>
    </div>
  )
}

export type Busy = {
  /** What's running, or null. */
  label: string | null
  /** Run `fn` behind the overlay. `fn` can change the label as it goes. A call made while one is
   *  already running is dropped and resolves to undefined. Errors pass through. */
  run: <T>(
    label: string,
    fn: (relabel: (label: string) => void) => Promise<T>
  ) => Promise<T | undefined>
  /** Render this somewhere in the page. */
  overlay: ReactNode
}

/** One slow thing at a time, with the window-wide loader shown while it runs. */
export function useBusy(): Busy {
  const [label, setLabel] = useState<string | null>(null)
  const running = useRef(false)
  const run = useCallback(
    async <T,>(text: string, fn: (relabel: (label: string) => void) => Promise<T>) => {
      if (running.current) return undefined
      running.current = true
      setLabel(text)
      try {
        return await fn(setLabel)
      } finally {
        running.current = false
        setLabel(null)
      }
    },
    []
  )
  return { label, run, overlay: label ? <LoadingOverlay label={label} /> : null }
}
