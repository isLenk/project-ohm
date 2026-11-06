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

export type Jump = { messageId: string; nonce: number } | null

export function useFeed() {
  const [state, setState] = useState<FeedState>({ items: [], authors: {}, next: null, prev: null, channel: null })
  const [loading, setLoading] = useState(false)
  const [jump, setJump] = useState<Jump>(null)
  const busy = useRef(false)

  const reset = useCallback(async (around?: string, scrollTo = true) => {
    setLoading(true)
    try {
      const q = around ? `around=${encodeURIComponent(around)}` : ''
      const [f, all] = await Promise.all([get<Feed>(`/feed?limit=${PAGE}&${q}`), get<{ authors: Record<string, Author> }>('/authors')])
      setState({ items: f.items, authors: { ...all.authors, ...f.authors }, next: f.next_cursor, prev: f.prev_cursor, channel: f.channel_id })
      if (around && scrollTo) setJump({ messageId: around, nonce: Date.now() })
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
            return { ...byId.get(it.id)!, conversation_id: it.conversation_id }
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
        it.type === 'conversation_start' && it.conversation.id === c.id ? { ...it, conversation: c } : it
      )
    }))
  }, [])

  const blocks = state.items.filter((i): i is Block => i.type === 'block')

  return { ...state, blocks, loading, jump, reset, more, apply, updateConversation, setJump }
}

export type FeedApi = ReturnType<typeof useFeed>
