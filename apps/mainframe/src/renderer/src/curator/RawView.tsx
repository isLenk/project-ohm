import { Fragment, useCallback, useEffect, useLayoutEffect, useMemo, useRef } from 'react'
import { LuCheck } from 'react-icons/lu'
import type { ConversationStats } from './api'
import type { FeedApi } from './useFeed'
import { Avatar, BotTag } from './Avatar'
import { approveHover, approveMark, chunks, cx, fmtDay, fmtRange, fmtTime, nameOf } from './util'

type Props = {
  feed: FeedApi
  /** Start a new conversation at this message. */
  onSplit: (messageId: string) => void
  /** Join this conversation onto the one before it. */
  onMerge: (c: ConversationStats) => void
  onError: (e: unknown) => void
  openInThreads: (messageId: string) => void
  onApprove: (c: ConversationStats) => void
  /** The conversation the header acts on, marked down the left edge. */
  currentId?: number
  /** The conversation an Approve button is hovered for: marked green. */
  approvingId: number | null
  onApproving: (id: number | null) => void
  /** Leave bots' messages out of the feed (they're never trained on anyway). */
  hideBots: boolean
}

export default function RawView({ feed, onSplit, onMerge, onError, openInThreads, onApprove, currentId, approvingId, onApproving, hideBots }: Props) {
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
    rows.current.get(feed.jump.messageId)?.scrollIntoView({ block: feed.jump.top ? 'start' : 'center' })
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

  // What's drawn: bots' blocks go when hideBots is on (a bot's messages are always its own blocks).
  const items = useMemo(
    () => (hideBots ? feed.items.filter((it) => it.type !== 'block' || !feed.authors[it.author_id]?.is_bot) : feed.items),
    [feed.items, feed.authors, hideBots]
  )
  const chunk = useMemo(() => chunks(items), [items])

  // Flatten blocks to lines so splits can go between any two messages.
  let prevAuthor: string | null = null
  let prevTs = 0
  let firstOfConversation = true

  return (
    <div ref={scroller} onScroll={onScroll} className="min-h-0 flex-1 overflow-auto px-8 pb-24">
      <div className="mx-auto max-w-[920px]">
        {feed.prev && <div className="py-3 text-center text-xs text-muted">Scroll up for earlier messages</div>}
        {items.map((it) => {
          if (it.type === 'conversation_start') {
            const c = it.conversation
            const isFirst = !feed.prev && items.findIndex((x) => x.type === 'conversation_start') === items.indexOf(it)
            firstOfConversation = true
            prevAuthor = null
            return (
              <div key={`c${c.id}`} className={cx('mt-5 flex flex-wrap items-center gap-3 pt-2 pb-2 text-[12.5px]', approveMark(chunk.get(`c${c.id}`), currentId, approvingId))}>
                <span className="font-semibold text-gray-100">
                  {fmtDay(c.first_ts)} · {fmtRange(c.first_ts, c.last_ts)}
                </span>
                {c.approved && <LuCheck className="size-3.5 text-[#6CCB98]" aria-label="Approved" />}
                <span className="h-px min-w-10 flex-1 bg-[#3f4049]" />
                {!isFirst && (
                  <button
                    onClick={() => onMerge(c)}
                    className="text-muted underline underline-offset-2 hover:text-gray-100"
                  >
                    Merge with above
                  </button>
                )}
                {!c.approved && (
                  <button
                    onClick={() => {
                      onApproving(null) // the button goes away, so it never gets a mouseleave
                      onApprove(c)
                    }}
                    {...approveHover(c.id, onApproving)}
                    className="text-muted underline underline-offset-2 hover:text-[#6CCB98]"
                  >
                    Approve
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
          const info = chunk.get(`b${b.id}`)
          const mark = approveMark(info, currentId, approvingId, { top: false, bottom: false })
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
                      <div className={cx('group/split relative h-2', mark)}>
                        <div className="absolute inset-x-0 top-1/2 flex -translate-y-1/2 items-center gap-2 pl-[88px] opacity-0 group-hover/split:opacity-100">
                          <span className="h-0 flex-1 border-t border-dashed border-[#4a4d55]" />
                          <button
                            onClick={() => onSplit(m.id)}
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
                        'grid grid-cols-[72px_180px_minmax(0,1fr)] items-baseline gap-4 py-1 text-[14.5px] leading-6 hover:bg-[#25262d]',
                        !mark && 'rounded',
                        // one row per line: only the block's first/last line take the chunk's corners
                        approveMark(info, currentId, approvingId, {
                          top: !!info?.top && i === 0,
                          bottom: !!info?.bottom && i === b.messages.length - 1
                        }),
                        feed.jump?.messageId === m.id && !feed.jump.top && 'bg-[#2B3A55]'
                      )}
                    >
                      <span className="text-right font-mono text-xs text-muted">{showName || i === 0 ? fmtTime(m.ts) : ''}</span>
                      <span className="flex min-w-0 items-center gap-2 font-semibold">
                        {showName && (
                          <>
                            <Avatar who={author} colorIndex={b.color_index} size={18} />
                            <span className="truncate">{nameOf(author)}</span>
                            {!!author?.is_bot && <BotTag />}
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
