// Client for curator_service (see Documentation/Curator/api.md).

export type Link = {
  parent_id: string | null
  status: 'accepted' | 'suggested' | 'root'
  origin: 'discord' | 'suggested' | 'manual'
  score: number | null
  parent_ts: number | null
  parent_preview: string | null
  parent_author_id: string | null
}

export type Message = {
  id: string
  ts: number
  content_text: string
  excluded: boolean
  exclude_reason: string | null
  mentions: string[]
  reply_to_id: string | null
  link: Link | null
}

export type Block = {
  type: 'block'
  id: string
  author_id: string
  color_index: number
  link: Link | null
  messages: Message[]
  conversation_id: number | null
}

export type ConversationStats = {
  id: number
  channel_id: string
  first_ts: number
  last_ts: number
  first_message_id: string
  last_message_id: string
  approved: boolean
  archived: boolean
  people: number
  messages: number
  skew: { author_id: string; count: number; share: number }[]
  unattached: number
  suggested: number
  threads: number
}

export type FeedItem = Block | { type: 'conversation_start'; conversation: ConversationStats }

export type Author = {
  id: string
  name: string
  display_name: string | null
  avatar_url: string | null
  is_bot: number
  color_index: number
}

export type Feed = {
  channel_id: string | null
  items: FeedItem[]
  authors: Record<string, Author>
  next_cursor: string | null
  prev_cursor: string | null
}

export type EditResult = {
  units: Block[]
  conversations_changed: { id: number; approved: boolean }[]
  inverse: { method: string; path: string; body: any }
  dirty: boolean
}

export class CuratorError extends Error {
  status: number
  code: string
  extra: any
  constructor(status: number, body: any) {
    super(body?.message || `Request failed (${status})`)
    this.status = status
    this.code = body?.error || 'error'
    this.extra = body || {}
  }
}

let config: Promise<{ url: string; token: string }> | null = null

function cfg() {
  if (!config) {
    config = window.api?.curatorConfig
      ? window.api.curatorConfig()
      : Promise.resolve({ url: 'http://127.0.0.1:8200', token: '' })
  }
  return config
}

export async function call<T = any>(method: string, path: string, body?: any): Promise<T> {
  const { url, token } = await cfg()
  let res: Response
  try {
    res = await fetch(url + path, {
      method,
      headers: { 'Content-Type': 'application/json', 'X-Curator-Token': token },
      body: body === undefined ? undefined : JSON.stringify(body)
    })
  } catch {
    throw new CuratorError(0, {
      error: 'offline',
      message: 'The curator service is not running. Check the app console for [curator] errors.'
    })
  }
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new CuratorError(res.status, data)
  return data as T
}

export const get = <T = any>(path: string) => call<T>('GET', path)
export const post = <T = any>(path: string, body: any = {}) => call<T>('POST', path, body)
export const put = <T = any>(path: string, body: any = {}) => call<T>('PUT', path, body)
export const patch = <T = any>(path: string, body: any = {}) => call<T>('PATCH', path, body)
export const del = <T = any>(path: string) => call<T>('DELETE', path)

const EVENT_NAMES = ['progress', 'stage', 'done', 'error', 'end', 'cancelled', 'note']

/** Follow a server-sent event stream. Returns a function that closes it. */
export function stream(path: string, onEvent: (name: string, data: any) => void): () => void {
  let source: EventSource | null = null
  let closed = false
  cfg().then(({ url, token }) => {
    if (closed) return
    const sep = path.includes('?') ? '&' : '?'
    source = new EventSource(`${url}${path}${sep}token=${encodeURIComponent(token)}`)
    for (const name of EVENT_NAMES) {
      source.addEventListener(name, (e: MessageEvent) => {
        let data: any = null
        try {
          data = JSON.parse(e.data)
        } catch {
          data = e.data
        }
        onEvent(name, data)
        if (name === 'end') source?.close()
      })
    }
    source.onerror = () => {
      // The server closes finished streams; don't reconnect forever.
      if (source?.readyState === EventSource.CLOSED) onEvent('closed', null)
    }
  })
  return () => {
    closed = true
    source?.close()
  }
}

/** Wait for a background job to finish, reporting progress. */
export function followJob(jobId: string, onProgress: (job: any) => void): Promise<any> {
  return new Promise((resolve, reject) => {
    const stop = stream(`/jobs/${jobId}/events`, (name, data) => {
      if (name !== 'progress' || !data) return
      onProgress(data)
      if (data.status === 'done') {
        stop()
        resolve(data.result)
      } else if (data.status === 'failed' || data.status === 'cancelled') {
        stop()
        reject(new Error(data.error || data.status))
      }
    })
  })
}

export const dialog = (method: 'showOpenDialog' | 'showSaveDialog', params: any) =>
  window.api?.openDialog ? window.api.openDialog(method, params) : Promise.resolve({ canceled: true })
