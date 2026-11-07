import { Fragment, useCallback, useEffect, useLayoutEffect, useRef } from 'react'
import { LuCheck } from 'react-icons/lu'
import { post } from './api'
import type { FeedApi } from './useFeed'
import { color, cx, fmtDay, fmtRange, fmtTime, nameOf } from './util'

type Props = {
  feed: FeedApi
  onChanged: () => void
  onError: (e: unknown) => void
  openInThreads: (messageId: string) => void
}

export default function RawView({ feed, onChanged, onError, openInThreads }: Props) {
  const scroller = useRef<HTMLDivElement>(null)
  const pendingUp = useRef<number | null>(null)
  const rows = useRef(new Map<string, HTMLElement>())

  useLayoutEffect(() => {
    if (pendingUp.current != null && scroller.current) {
      scroller.current.scrollTop += scroller.current.scrollHeight - pendingUp.current
      pendingUp.current = null
    }
  }, [feed.items])

  useEffect(() => {
    if (!feed.jump) return
    rows.current.get(feed.jump.messageId)?.scrollIntoView({ block: 'center' })
  }, [feed.jump])

  const onScroll = useCallback(() => {
    const s = scroller.current
    if (!s) return
    if (s.scrollTop + s.clientHeight > s.scrollHeight - 600 && feed.next) feed.more('down')
    if (s.scrollTop < 300 && feed.prev && pendingUp.current == null) {
      pendingUp.current = s.scrollHeight
      feed.more('up').then((ok) => {
        if (!ok) pendingUp.current = null
      })
    }
  }, [feed])

  const act = async (p: Promise<any>, anchor: string) => {
    try {
      await p
      await feed.reset(anchor)
      onChanged()
    } catch (e) {
      onError(e)
    }
  }

  // Flatten blocks to lines so splits can go between any two messages.
  let prevAuthor: string | null = null
  let prevTs = 0
  let firstOfConversation = true

  return (
    <div ref={scroller} onScroll={onScroll} className="min-h-0 flex-1 overflow-auto px-8 pb-24">
      <div className="mx-auto max-w-[920px]">
        {feed.prev && <div className="py-3 text-center text-xs text-muted">Scroll up for earlier messages</div>}
        {feed.items.map((it) => {
          if (it.type === 'conversation_start') {
            const c = it.conversation
            const isFirst = !feed.prev && feed.items.findIndex((x) => x.type === 'conversation_start') === feed.items.indexOf(it)
            firstOfConversation = true
            prevAuthor = null
            return (
              <div key={`c${c.id}`} className="flex flex-wrap items-center gap-3 pt-7 pb-2 text-[12.5px]">
                <span className="font-semibold text-gray-100">
                  {fmtDay(c.first_ts)} · {fmtRange(c.first_ts, c.last_ts)}
                </span>
                {c.approved && <LuCheck className="size-3.5 text-[#6CCB98]" aria-label="Approved" />}
                <span className="h-px min-w-10 flex-1 bg-[#3f4049]" />
                {!isFirst && (
                  <button
                    onClick={() => act(post(`/conversations/${c.id}/merge-up`), c.first_message_id)}
                    className="text-muted underline underline-offset-2 hover:text-gray-100"
                  >
                    Merge with above
                  </button>
                )}
                <button
                  onClick={() => openInThreads(c.first_message_id)}
                  className="text-muted underline underline-offset-2 hover:text-gray-100"
                >
                  Open in Threads
                </button>
              </div>
            )
          }
          const b = it
          const author = feed.authors[b.author_id]
          return (
            <Fragment key={`b${b.id}`}>
              {b.messages.map((m, i) => {
                const showName = i === 0 && prevAuthor !== b.author_id
                const gap = prevTs && !firstOfConversation ? m.ts - prevTs : 0
                const canSplit = !firstOfConversation
                firstOfConversation = false
                prevAuthor = b.author_id
                prevTs = m.ts
                return (
                  <Fragment key={m.id}>
                    {canSplit && (
                      <div className="group/split relative h-2">
                        <div className="absolute inset-x-0 top-1/2 flex -translate-y-1/2 items-center gap-2 pl-[88px] opacity-0 group-hover/split:opacity-100">
                          <span className="h-0 flex-1 border-t border-dashed border-[#4a4d55]" />
                          <button
                            onClick={() => act(post('/conversations/split', { before_message_id: m.id }), m.id)}
                            className="rounded border border-line bg-page px-2 py-0.5 text-xs text-gray-200 hover:bg-raised"
                          >
                            Split here
                          </button>
                          {gap > 60_000 && <span className="font-mono text-[11px] text-muted">{Math.round(gap / 60000)} min gap</span>}
                        </div>
                      </div>
                    )}
                    <div
                      ref={(el) => {
                        if (el) rows.current.set(m.id, el)
                        else rows.current.delete(m.id)
                      }}
                      className={cx(
                        'grid grid-cols-[72px_180px_minmax(0,1fr)] items-baseline gap-4 rounded py-1 text-[14.5px] leading-6 hover:bg-[#25262d]',
                        feed.jump?.messageId === m.id && 'bg-[#2B3A55]'
                      )}
                    >
                      <span className="text-right font-mono text-xs text-muted">{showName || i === 0 ? fmtTime(m.ts) : ''}</span>
                      <span className="flex min-w-0 items-center gap-2 font-semibold">
                        {showName && (
                          <>
                            <span className="size-2 shrink-0 rounded-full" style={{ background: color(b.color_index) }} />
                            <span className="truncate">{nameOf(author)}</span>
                          </>
                        )}
                      </span>
                      <span className={cx('break-words whitespace-pre-wrap', m.excluded ? 'text-muted line-through' : 'text-[#D6D7DB]')}>
                        {m.content_text}
                      </span>
                    </div>
                  </Fragment>
                )
              })}
            </Fragment>
          )
        })}
        {feed.next && <div className="py-6 text-center text-xs text-muted">Loading more…</div>}
        {!feed.items.length && !feed.loading && (
          <div className="py-24 text-center text-sm text-muted">No messages yet. Import a Discord export to start.</div>
        )}
      </div>
    </div>
  )
}
