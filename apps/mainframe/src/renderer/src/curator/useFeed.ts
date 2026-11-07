import { useCallback, useRef, useState } from 'react'
import { get, type Author, type Block, type ConversationStats, type EditResult, type Feed, type FeedItem } from './api'

const PAGE = 80

export type FeedState = {
  items: FeedItem[]
  authors: Record<string, Author>
  next: string | null
  prev: string | null
  channel: string | null
}

/** `top`: scroll the message's conversation to the top of the view, without the flash (opening a project). */
export type Jump = { messageId: string; nonce: number; top?: boolean } | null

export function useFeed() {
  const [state, setState] = useState<FeedState>({ items: [], authors: {}, next: null, prev: null, channel: null })
  const [loading, setLoading] = useState(false)
  const [jump, setJump] = useState<Jump>(null)
  const busy = useRef(false)

  const reset = useCallback(async (around?: string, scrollTo: boolean | 'top' = true) => {
    setLoading(true)
    try {
      const q = around ? `around=${encodeURIComponent(around)}` : ''
      const [f, all] = await Promise.all([get<Feed>(`/feed?limit=${PAGE}&${q}`), get<{ authors: Record<string, Author> }>('/authors')])
      setState({ items: f.items, authors: { ...all.authors, ...f.authors }, next: f.next_cursor, prev: f.prev_cursor, channel: f.channel_id })
      if (around && scrollTo) setJump({ messageId: around, nonce: Date.now(), top: scrollTo === 'top' })
    } finally {
      setLoading(false)
    }
  }, [])

  const more = useCallback(
    async (direction: 'down' | 'up') => {
      const cursor = direction === 'down' ? state.next : state.prev
      if (!cursor || busy.current) return false
      busy.current = true
      try {
        const ch = state.channel ? `&channel_id=${encodeURIComponent(state.channel)}` : ''
        const f = await get<Feed>(`/feed?limit=${PAGE}&cursor=${cursor}&direction=${direction}${ch}`)
        setState((s) => ({
          ...s,
          items: direction === 'down' ? [...s.items, ...f.items] : [...f.items, ...s.items],
          authors: { ...s.authors, ...f.authors },
          next: direction === 'down' ? f.next_cursor : s.next,
          prev: direction === 'up' ? f.prev_cursor : s.prev
        }))
        return true
      } finally {
        busy.current = false
      }
    },
    [state.next, state.prev, state.channel]
  )

  /** Swap in the blocks an edit returned, and update approval on changed conversations. */
  const apply = useCallback((r: Partial<EditResult>) => {
    setState((s) => {
      const byId = new Map((r.units || []).map((u) => [u.id, u]))
      const changed = new Map((r.conversations_changed || []).map((c) => [c.id, c.approved]))
      return {
        ...s,
        items: s.items.map((it) => {
          if (it.type === 'block' && byId.has(it.id)) {
            const approved = it.conversation_id != null ? changed.get(it.conversation_id) : undefined
            return {
              ...byId.get(it.id)!,
              conversation_id: it.conversation_id,
              conversation_approved: approved ?? it.conversation_approved
            }
          }
          if (it.type === 'block' && it.conversation_id != null && changed.has(it.conversation_id)) {
            return { ...it, conversation_approved: changed.get(it.conversation_id)! }
          }
          if (it.type === 'conversation_start' && changed.has(it.conversation.id)) {
            return { ...it, conversation: { ...it.conversation, approved: changed.get(it.conversation.id)! } }
          }
          return it
        })
      }
    })
  }, [])

  const updateConversation = useCallback((c: ConversationStats) => {
    setState((s) => ({
      ...s,
      items: s.items.map((it) =>
        it.type === 'conversation_start' && it.conversation.id === c.id
          ? { ...it, conversation: c }
          : it.type === 'block' && it.conversation_id === c.id
            ? { ...it, conversation_approved: c.approved }
            : it
      )
    }))
  }, [])

  /** Approving a conversation accepts its suggested arrows (conversations.approve on the service).
   *  Mirror that in place, so the loaded feed and its scroll position stay put. */
  const acceptSuggested = useCallback((conversationId: number) => {
    const accept = <L extends { status: string } | null>(link: L): L =>
      link && link.status === 'suggested' ? { ...link, status: 'accepted' } : link
    setState((s) => ({
      ...s,
      items: s.items.map((it) =>
        it.type === 'block' && it.conversation_id === conversationId
          ? { ...it, link: accept(it.link), messages: it.messages.map((m) => ({ ...m, link: accept(m.link) })) }
          : it
      )
    }))
  }, [])

  const blocks = state.items.filter((i): i is Block => i.type === 'block')

  return { ...state, blocks, loading, jump, reset, more, apply, updateConversation, acceptSuggested, setJump }
}

export type FeedApi = ReturnType<typeof useFeed>
