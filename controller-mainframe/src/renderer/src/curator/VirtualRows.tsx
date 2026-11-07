import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'

// A scroll box that only mounts the rows on screen (plus a few either side). Rows must all be rowHeight tall.
// Big projects have thousands of conversations; mounting them all made the sidebar lag.
export function VirtualRows<T>({
  items,
  rowHeight,
  row,
  className,
  activeIndex,
  children,
  overscan = 8
}: {
  items: T[]
  rowHeight: number
  row: (item: T, index: number) => ReactNode
  className?: string
  /** Scrolled into view when it changes (e.g. the conversation you jumped to from search). */
  activeIndex?: number
  /** Rendered after the rows, e.g. empty states. */
  children?: ReactNode
  overscan?: number
}) {
  const box = useRef<HTMLDivElement>(null)
  const [scroll, setScroll] = useState(0)
  const [height, setHeight] = useState(800)

  useLayoutEffect(() => {
    const el = box.current
    if (!el) return
    setHeight(el.clientHeight)
    const ro = new ResizeObserver(() => setHeight(el.clientHeight))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  useEffect(() => {
    const el = box.current
    if (!el || activeIndex == null || activeIndex < 0) return
    const top = activeIndex * rowHeight
    if (top < el.scrollTop || top + rowHeight > el.scrollTop + el.clientHeight) {
      el.scrollTop = Math.max(0, top - el.clientHeight / 3)
    }
  }, [activeIndex, rowHeight])

  // The list can shrink under the scroll position (a filter, the Approved tab).
  useEffect(() => {
    const el = box.current
    if (el && el.scrollTop > items.length * rowHeight) el.scrollTop = 0
  }, [items.length, rowHeight])

  const first = Math.max(0, Math.floor(scroll / rowHeight) - overscan)
  const last = Math.min(items.length, Math.ceil((scroll + height) / rowHeight) + overscan)

  return (
    <div ref={box} className={className} onScroll={(e) => setScroll(e.currentTarget.scrollTop)}>
      <div style={{ height: items.length * rowHeight, position: 'relative' }}>
        <div style={{ position: 'absolute', top: first * rowHeight, left: 0, right: 0 }}>
          {items.slice(first, last).map((item, i) => row(item, first + i))}
        </div>
      </div>
      {children}
    </div>
  )
}
