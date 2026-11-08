import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { LuAlignLeft, LuArchive, LuBot, LuBrain, LuCheck, LuFilePlus, LuGitBranch, LuLoader, LuMerge, LuSearch, LuSparkles, LuWandSparkles, LuX } from 'react-icons/lu'
import { CuratorError, call, dialog, followJob, get, post, type BulkApproved, type Candidate, type ConversationStats, type EditResult, type Restore } from './api'
import { useFeed } from './useFeed'
import { useHistory, type Step } from './history'
import { Avatar } from './Avatar'
import { VirtualRows } from './VirtualRows'
import { FilterButton, NO_FILTERS, activeCount, applyFilters, loadFilters, saveFilters, type ConvFilters } from './ConversationFilters'
import ThreadsView from './ThreadsView'
import RawView from './RawView'
import MemoriesView from './MemoriesView'
import SearchPalette from './SearchPalette'
import TrainModal from './TrainModal'
import EvalModal from './EvalModal'
import FindGood from './FindGood'
import { TermPicker } from './TermPicker'
import { ModelMenu, SaveMenu } from './Menus'
import { approveHover, color, cx, fmtDay, fmtDuration, fmtRange, pct } from './util'
import { LoadingOverlay, Spinner, useBusy } from '../components/Loader'

type View = 'threads' | 'raw' | 'memories'
type Filter = 'all' | 'approved' | 'archived'

/** What undo/redo steps call on the page (always this render's versions). */
type Ops = {
  applyEdit: (r: EditResult) => void
  applyConv: (s: ConversationStats, focus: boolean) => void
  doApprove: (id: number, confirmed?: boolean) => Promise<Restore[]>
  doSplit: (messageId: string) => Promise<{ before?: ConversationStats; after?: ConversationStats; was: boolean }>
  doMerge: (id: number, firstMessageId: string) => Promise<ConversationStats>
  mergeApprovals: (c: ConversationStats) => { aboveWas: boolean; ownWas: boolean }
  doBulk: (ids: number[]) => Promise<BulkApproved>
  refreshAll: () => Promise<void>
}
type ProjectInfo = { open: boolean; path?: string; name?: string; dirty?: boolean; saved_at?: number | null; recent?: string[] }

// Sidebar rows are a fixed height so the list can be virtualized.
const ROW_HEIGHT = 72

const OHM_FILTER = [{ name: 'Ohm project', extensions: ['ohm'] }]
// DiscordChatExporter .json, a messages.jsonl dump, and the speakers.json that comes with it.
const IMPORT_FILTER = [{ name: 'Discord export', extensions: ['json', 'jsonl'] }]
const fileName = (path: string) => path.split(/[\\/]/).pop()

export default function CuratorPage() {
  const feed = useFeed()
  const [project, setProject] = useState<ProjectInfo | null>(null)
  const [view, setView] = useState<View>('threads')
  const [filter, setFilter] = useState<Filter>('all')
  const [convs, setConvs] = useState<ConversationStats[]>([])
  // Which tab `convs` was fetched for. Until it matches `filter` the list is still the old tab's, so show a spinner.
  const [convsFor, setConvsFor] = useState<Filter | null>(null)
  const filterNow = useRef(filter)
  filterNow.current = filter
  const [current, setCurrent] = useState<ConversationStats | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const [searching, setSearching] = useState(false)
  const [training, setTraining] = useState(false)
  const [evaluating, setEvaluating] = useState<string | null>(null)
  // Find good conversations: 'hidden' while you look at one of its sample in the feed (it keeps its state).
  const [finding, setFinding] = useState<'open' | 'hidden' | null>(null)
  const [activeRun, setActiveRun] = useState<{ overall: number } | null>(null)
  const [confirm, setConfirm] = useState<{ conv: ConversationStats; suggested: number; unattached: number } | null>(null)
  const [person, setPerson] = useState<string | null>(null)
  const [modelsKey, setModelsKey] = useState(0)
  const [recover, setRecover] = useState<string | null>(null)
  const [guessing, setGuessing] = useState<string | null>(null)
  // Leave bots out of the feed entirely. A view setting, so it's kept per app, not per project.
  const [hideBots, setHideBots] = useState(() => localStorage.getItem('curator.hideBots') === '1')
  const toggleBots = () =>
    setHideBots((on) => {
      localStorage.setItem('curator.hideBots', on ? '0' : '1')
      return !on
    })
  // An Approve button is hovered or focused: the feed highlights the conversation it would approve.
  const [approvingId, setApprovingId] = useState<number | null>(null)
  const [importing, setImporting] = useState<string | null>(null)
  const [listFilters, setListFilters] = useState<ConvFilters>(NO_FILTERS)
  const [guessAll, setGuessAll] = useState<{ id: string; done: number; total: number; note: string | null; left: number | null } | null>(null)

  const onError = useCallback((e: unknown) => {
    const msg = e instanceof Error ? e.message : String(e)
    setError(msg)
  }, [])

  const say = (msg: string) => {
    setToast(msg)
    setTimeout(() => setToast((t) => (t === msg ? null : t)), 3500)
  }

  const markDirty = useCallback(() => setProject((p) => (p ? { ...p, dirty: true } : p)), [])
  // Opening, saving and anything else slow runs behind the window-wide loader.
  const busy = useBusy()
  const history = useHistory(onError, say)

  // Set when a project opens or an import lands: the feed then starts at the top of the list
  // (the newest conversation), not the channel's oldest message. Otherwise the feed reports that
  // oldest conversation as current and the sidebar scrolls to the bottom to show it.
  const openAtTop = useRef(false)

  const loadConvs = useCallback(async () => {
    try {
      const r = await get<{ conversations: ConversationStats[] }>(`/conversations?filter=${filter}`)
      if (filterNow.current !== filter) return // switched tabs while this was loading
      setConvs(r.conversations)
      setConvsFor(filter)
      if (openAtTop.current) {
        openAtTop.current = false
        const top = r.conversations[0]
        await feed.reset(top?.first_message_id, top ? 'top' : true)
      }
    } catch (e) {
      if (!(e instanceof CuratorError && e.code === 'no_project')) onError(e)
      if (filterNow.current === filter) setConvsFor(filter) // don't spin forever; the error banner explains
      if (openAtTop.current) {
        openAtTop.current = false
        await feed.reset().catch(() => {}) // the error banner already explains
      }
    }
  }, [filter, onError]) // eslint-disable-line react-hooks/exhaustive-deps

  /** Put fresh stats for one conversation into the sidebar without refetching the whole list
   *  (that list can be thousands long after a big import). */
  const patchConvs = useCallback(
    (updates: (Partial<ConversationStats> & { id: number })[]) => {
      if (!updates.length) return
      const fits = (c: ConversationStats) =>
        filter === 'all' ? !c.archived : filter === 'approved' ? c.approved && !c.archived : c.archived
      setConvs((list) => {
        const byId = new Map(updates.map((u) => [u.id, u]))
        const next: ConversationStats[] = []
        for (const c of list) {
          const u = byId.get(c.id)
          byId.delete(c.id)
          const merged = u ? { ...c, ...u } : c
          if (fits(merged)) next.push(merged)
        }
        // Full stats for a conversation that just started matching the tab (approved while on "Approved").
        for (const u of byId.values()) if ('first_ts' in u && fits(u as ConversationStats)) next.push(u as ConversationStats)
        return next.sort((a, b) => b.first_ts - a.first_ts)
      })
    },
    [filter]
  )

  const loadProject = useCallback(async () => {
    try {
      const p = await get<ProjectInfo>('/project')
      setProject(p)
      if (p.open) {
        openAtTop.current = true
        setConvsFor(null)
        loadConvs()
      }
    } catch (e) {
      onError(e)
      setProject({ open: false })
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    loadProject()
  }, [loadProject])

  useEffect(() => {
    if (project?.open) loadConvs()
  }, [filter, project?.open, loadConvs])

  // ── project file ────────────────────────────────────────────────────────
  /** Show a project that just opened. Resolves once its list and feed are in. */
  const opened = async (p: any) => {
    setProject({ open: true, ...p })
    history.clear()
    openAtTop.current = true
    setConvsFor(null)
    await loadConvs()
  }

  const openPath = (path: string, recoverChoice?: boolean) =>
    busy.run(recoverChoice ? 'Restoring unsaved changes…' : `Opening ${fileName(path)}…`, async (relabel) => {
      try {
        const p = await post('/project/open', { path, recover: recoverChoice ?? null })
        setRecover(null)
        relabel('Loading conversations…')
        await opened(p)
      } catch (e) {
        if (e instanceof CuratorError && e.code === 'recovery_available') setRecover(path)
        else onError(e)
      }
    })

  const openProject = async () => {
    const r = await dialog('showOpenDialog', { properties: ['openFile'], filters: OHM_FILTER })
    if (!r.canceled && r.filePaths?.[0]) openPath(r.filePaths[0])
  }

  const newProject = async () => {
    const r = await dialog('showSaveDialog', { title: 'New Curator project', defaultPath: 'conversations.ohm', filters: OHM_FILTER })
    if (r.canceled || !r.filePath) return
    await busy.run('Creating project…', async () => {
      try {
        await opened(await post('/project/new', { path: r.filePath }))
      } catch (e) {
        onError(e)
      }
    })
  }

  const save = useCallback(
    () =>
      busy.run('Saving…', async () => {
        try {
          const r = await post('/project/save')
          setProject((p) => (p ? { ...p, dirty: false, saved_at: r.saved_at } : p))
          say('Saved')
        } catch (e) {
          onError(e)
        }
      }),
    [onError, busy.run] // eslint-disable-line react-hooks/exhaustive-deps
  )

  const saveAs = useCallback(async () => {
    const r = await dialog('showSaveDialog', { title: 'Save project to file', defaultPath: project?.path, filters: OHM_FILTER })
    if (r.canceled || !r.filePath) return
    await busy.run(`Saving to ${fileName(r.filePath)}…`, async () => {
      try {
        const s = await post('/project/save-as', { path: r.filePath })
        setProject((p) => (p ? { ...p, dirty: false, saved_at: s.saved_at, path: s.path } : p))
        say('Saved')
      } catch (e) {
        onError(e)
      }
    })
  }, [project?.path, onError, busy.run]) // eslint-disable-line react-hooks/exhaustive-deps

  const exportJsonl = async () => {
    const r = await dialog('showSaveDialog', { title: 'Export approved conversations', defaultPath: 'training.jsonl', filters: [{ name: 'JSONL', extensions: ['jsonl'] }] })
    if (r.canceled || !r.filePath) return
    await busy.run('Exporting approved conversations…', async () => {
      try {
        const out = await post('/export/jsonl', { path: r.filePath })
        say(`Exported ${out.examples} examples`)
      } catch (e) {
        onError(e)
      }
    })
  }

  // ── edits & undo ────────────────────────────────────────────────────────
  // Undo steps run later, so they reach the page through `ops`, which always holds this render's
  // functions; a step's own closure would see stale state (current conversation, list tab, feed).
  const ops = useRef<Ops>(null!)

  /** Show an edit's result: swapped units, approval it cleared, fresh stats for the header. */
  const applyEdit = (r: EditResult) => {
    feed.apply(r)
    markDirty()
    patchConvs(r.conversations_changed || [])
    if (!current) return
    const touched = r.conversations_changed?.some((c) => c.id === current.id)
    get<ConversationStats>(`/conversations/${current.id}`)
      .then((c) => {
        setCurrent(c)
        if (touched) feed.updateConversation(c)
        patchConvs([c])
      })
      .catch(() => {})
  }

  /** Show a conversation's new stats everywhere. The header moves to it only if `focus`. */
  const applyConv = (s: ConversationStats, focus: boolean) => {
    setCurrent((cur) => (focus || !cur || cur.id === s.id ? s : cur))
    feed.updateConversation(s)
    markDirty()
    patchConvs([s])
  }

  // Arrow and exclude edits: the service hands back the request that reverses each one.
  const editStep = (inv: EditResult['inverse']): Step => ({
    label: inv.path.startsWith('/messages/') ? 'exclude' : 'arrow change',
    run: async () => {
      const r = await call<EditResult>(inv.method, inv.path, inv.body)
      ops.current.applyEdit(r)
      return r.inverse ? editStep(r.inverse) : null
    }
  })

  const onEdit = useCallback(
    (r: EditResult) => {
      ops.current.applyEdit(r)
      if (r.inverse) history.record(editStep(r.inverse))
    },
    [history.record] // eslint-disable-line react-hooks/exhaustive-deps
  )

  // ── list filters (saved per project file) ──────────────────────────────
  useEffect(() => setListFilters(loadFilters(project?.path)), [project?.path])
  const changeFilters = (f: ConvFilters) => {
    setListFilters(f)
    saveFilters(project?.path, f)
  }
  const convsLoading = convsFor !== filter
  const visible = useMemo(() => (convsLoading ? [] : applyFilters(convs, listFilters)), [convs, listFilters, convsLoading])
  const filtering = activeCount(listFilters) > 0
  const currentId = current?.id
  const activeRow = useMemo(() => visible.findIndex((cv) => cv.id === currentId), [visible, currentId])

  // ── import ──────────────────────────────────────────────────────────────
  const importFiles = async () => {
    const r = await dialog('showOpenDialog', {
      title: 'Import Discord chats',
      buttonLabel: 'Import',
      properties: ['openFile', 'multiSelections'],
      filters: IMPORT_FILTER
    })
    if (r.canceled || !r.filePaths?.length) return
    setImporting('Starting import…')
    try {
      const j = await post('/imports', { paths: r.filePaths })
      const s = await followJob(j.id, (p) => {
        const step = p.note || 'Importing…'
        setImporting(p.total ? `${step} ${pct(p.done / p.total)}` : step)
      })
      setProject({ open: true, ...(await get('/project')) })
      history.clear()
      openAtTop.current = true
      loadConvs()
      const skipped = (s.files || []).filter((f: any) => f.status === 'skipped' && f.kind !== 'conversations')
      say(
        s.messages
          ? `Imported ${s.messages.toLocaleString()} messages · ${s.conversations.toLocaleString()} conversations` +
              (s.duplicates ? ` · ${s.duplicates.toLocaleString()} already here` : '') +
              (s.empty ? ` · ${s.empty.toLocaleString()} without text left out` : '') +
              (skipped.length ? ` · skipped ${skipped.map((f: any) => f.file).join(', ')}` : '')
          : 'Nothing new in those files'
      )
    } catch (e) {
      onError(e)
    } finally {
      setImporting(null)
    }
  }

  /** Was conversation `id` approved, as far as the loaded feed and the list know. */
  const isApproved = (id: number | null | undefined) => {
    if (id == null) return false
    for (const it of feed.items) {
      if (it.type === 'conversation_start' && it.conversation.id === id) return it.conversation.approved
      if (it.type === 'block' && it.conversation_id === id && it.conversation_approved != null) return it.conversation_approved
    }
    return convs.find((c) => c.id === id)?.approved ?? false
  }

  // Splitting and merging clear approval. Undo puts it back, so it remembers who was approved.
  const mergeApprovals = (c: ConversationStats) => {
    let above: number | null = null
    for (const it of feed.items) {
      if (it.type !== 'conversation_start') continue
      if (it.conversation.id === c.id) break
      if (it.conversation.channel_id === c.channel_id) above = it.conversation.id
    }
    if (above == null) {
      above = convs.filter((x) => x.channel_id === c.channel_id && x.first_ts < c.first_ts).sort((x, y) => y.first_ts - x.first_ts)[0]?.id ?? null
    }
    return { aboveWas: isApproved(above), ownWas: isApproved(c.id) }
  }

  const doMerge = async (id: number, firstMessageId: string) => {
    const s = await post<ConversationStats>(`/conversations/${id}/merge-up`)
    markDirty()
    setCurrent(s)
    await feed.reset(firstMessageId)
    loadConvs()
    return s
  }

  const doSplit = async (messageId: string) => {
    const convId = feed.blocks.find((b) => b.messages.some((m) => m.id === messageId))?.conversation_id
    const was = isApproved(convId)
    const r = await post<{ conversations: ConversationStats[] }>('/conversations/split', { before_message_id: messageId })
    markDirty()
    const [before, after] = r.conversations
    if (after) setCurrent(after) // the new conversation, starting at messageId
    await feed.reset(messageId)
    loadConvs()
    return { before, after, was }
  }

  // undoes a split: merge the new conversation back, re-approving if the split one was approved
  const splitUndo = (after: ConversationStats, was: boolean): Step => ({
    label: 'split',
    run: async () => {
      const merged = await ops.current.doMerge(after.id, after.first_message_id)
      if (was) await ops.current.doApprove(merged.id)
      return splitStep(after.first_message_id)
    }
  })
  const splitStep = (messageId: string): Step => ({
    label: 'split',
    run: async () => {
      const { after, was } = await ops.current.doSplit(messageId)
      return after ? splitUndo(after, was) : null
    }
  })
  // undoes a merge: split it apart again and restore both halves' approval
  const mergeUndo = (firstMessageId: string, aboveWas: boolean, ownWas: boolean): Step => ({
    label: 'merge',
    run: async () => {
      const { before, after } = await ops.current.doSplit(firstMessageId)
      if (aboveWas && before) await ops.current.doApprove(before.id)
      if (ownWas && after) await ops.current.doApprove(after.id)
      return after ? mergeStep(after) : null
    }
  })
  const mergeStep = (c: ConversationStats): Step => ({
    label: 'merge',
    run: async () => {
      const { aboveWas, ownWas } = ops.current.mergeApprovals(c)
      await ops.current.doMerge(c.id, c.first_message_id)
      return mergeUndo(c.first_message_id, aboveWas, ownWas)
    }
  })

  // ── join a conversation onto the one before it ──────────────────────────
  const mergeUp = (c: ConversationStats) =>
    busy.run('Merging conversations…', async () => {
      try {
        const { aboveWas, ownWas } = mergeApprovals(c)
        const s = await doMerge(c.id, c.first_message_id)
        history.record(mergeUndo(c.first_message_id, aboveWas, ownWas))
        say(`Merged into one conversation · ${s.messages} messages, ${s.people} people. Ctrl+Z undoes it.`)
      } catch (e) {
        onError(e)
      }
    })

  // ── start a new conversation at a message ───────────────────────────────
  const splitAt = (messageId: string) =>
    busy.run('Splitting conversation…', async () => {
      try {
        const { after, was } = await doSplit(messageId)
        if (after) history.record(splitUndo(after, was))
        say('Split into a new conversation. Ctrl+Z undoes it.')
      } catch (e) {
        onError(e)
      }
    })

  // ── model guesses for every unattached block in the project ─────────────
  const followGuessAll = useCallback(
    async (id: string) => {
      setGuessAll({ id, done: 0, total: 0, note: null, left: null })
      let start: { t: number; done: number } | null = null
      let last = ''
      try {
        const r = await followJob(id, (p) => {
          last = p.note || last
          if (!start && p.total) start = { t: Date.now(), done: p.done }
          const rate = start && p.done > start.done ? (p.done - start.done) / ((Date.now() - start.t) / 1000) : 0
          setGuessAll({ id, done: p.done, total: p.total, note: p.note, left: rate ? (p.total - p.done) / rate : null })
        })
        if (r?.stopped) onError(new Error(`${r.stopped} ${r.suggested} guesses made before it stopped. Start llama-server and run Guess all again to continue.`))
        else say(`Guess all finished · ${r?.suggested ?? 0} guesses`)
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        if (msg === 'cancelled') say(`Guess all stopped · ${last || 'no guesses'}. Run it again to pick up where it left off.`)
        else onError(e)
      } finally {
        setGuessAll(null)
        markDirty()
        feed.reset()
        loadConvs()
      }
    },
    [feed, loadConvs, markDirty, onError] // eslint-disable-line react-hooks/exhaustive-deps
  )

  const startGuessAll = async () => {
    try {
      const { blocks } = await get<{ blocks: number }>('/links/guess/pending')
      if (!blocks) return say('Every block already has an arrow or a model guess')
      const est = fmtDuration(blocks * 0.16) // measured ~0.15 s per block on the 3090 with Gemma 4 E4B
      const ok = window.confirm(
        `Ask the model about ${blocks.toLocaleString()} unattached blocks?

` +
          `Roughly ${est} (h:mm:ss) on this GPU. You can keep working while it runs, and cancel any time; ` +
          `a later run picks up where it stopped. Training a LoRA stops it, because training needs the GPU.

` +
          `Newest conversations go first. Its arrows show as dashed predictions; approving a conversation accepts them. Save the project when it's done.`
      )
      if (!ok) return
      const j = await post('/links/guess', {})
      followGuessAll(j.id)
    } catch (e) {
      onError(e)
    }
  }

  const cancelGuessAll = async () => {
    if (guessAll) await post(`/jobs/${guessAll.id}/cancel`).catch(onError)
  }

  // Reattach to a run that was going before the window reloaded.
  useEffect(() => {
    if (!project?.open) return
    get<{ jobs: any[] }>('/jobs')
      .then((r) => {
        const running = r.jobs.find((j) => j.kind === 'link_guess_all' && j.status === 'running')
        if (running) followGuessAll(running.id)
      })
      .catch(() => {})
  }, [project?.open]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── model guesses for unattached blocks ─────────────────────────────────
  const guessReplies = async (c: ConversationStats) => {
    setGuessing('Asking the model…')
    try {
      const j = await post('/links/guess', { conversation_id: c.id })
      const r = await followJob(j.id, (p) => setGuessing(p.total ? `Asking the model ${p.done}/${p.total}` : 'Asking the model…'))
      feed.apply({ units: r?.units || [] })
      if (r?.suggested) markDirty()
      const s = await get<ConversationStats>(`/conversations/${c.id}`)
      setCurrent(s)
      feed.updateConversation(s)
      patchConvs([s])
      if (r?.stopped) onError(new Error(`${r.stopped} ${r.suggested} guesses made before it stopped.`))
      else say(r?.suggested ? `The model guessed ${r.suggested} ${r.suggested === 1 ? 'reply' : 'replies'}` : 'The model had no guesses')
    } catch (e) {
      onError(e)
    } finally {
      setGuessing(null)
    }
  }

  // ── approval ────────────────────────────────────────────────────────────
  /** Approve; returns the suggestions it accepted, so undo can put them back. */
  const doApprove = async (id: number, confirmed = true) => {
    const { accepted, ...s } = await post<ConversationStats & { accepted?: Restore[] }>(`/conversations/${id}/approve`, { confirm: confirmed })
    // Approving from a feed header leaves the header on the conversation you were reading.
    applyConv(s, false)
    feed.acceptSuggested(s.id) // suggested arrows just became solid; reloading the feed would jump the scroll
    return accepted || []
  }

  // undoes an approval: the accepted guesses go back to suggestions, then approval comes off
  const approvedUndo = (id: number, accepted: Restore[], label: string): Step => ({
    label,
    run: async () => {
      for (const a of accepted) feed.apply(await post<EditResult>('/links/restore', a))
      ops.current.applyConv(await post<ConversationStats>(`/conversations/${id}/unapprove`), false)
      return approveStep(id, label)
    }
  })
  const approveStep = (id: number, label: string): Step => ({
    label,
    run: async () => approvedUndo(id, await ops.current.doApprove(id), label)
  })

  const approve = async (c: ConversationStats, confirmed = false) => {
    try {
      const accepted = await doApprove(c.id, confirmed)
      setConfirm(null)
      history.record(approvedUndo(c.id, accepted, 'approve'))
    } catch (e) {
      if (e instanceof CuratorError && e.code === 'needs_confirm') setConfirm({ conv: c, suggested: e.extra.suggested, unattached: e.extra.unattached })
      else onError(e)
    }
  }

  // archive <-> unarchive; unapprove is undone by approving again
  const flagStep = (id: number, path: 'archive' | 'unarchive'): Step => ({
    label: path,
    run: async () => {
      ops.current.applyConv(await post<ConversationStats>(`/conversations/${id}/${path}`), true)
      return flagStep(id, path === 'archive' ? 'unarchive' : 'archive')
    }
  })

  const setFlag = async (c: ConversationStats, path: 'archive' | 'unarchive' | 'unapprove') => {
    try {
      applyConv(await post<ConversationStats>(`/conversations/${c.id}/${path}`), true)
      history.record(path === 'unapprove' ? approveStep(c.id, 'unapprove') : flagStep(c.id, path === 'archive' ? 'unarchive' : 'archive'))
    } catch (e) {
      onError(e)
    }
  }

  // ── bulk approval (Find good conversations) ─────────────────────────────
  /** After many conversations changed at once: refetch the list and feed rather than patch them. */
  const refreshAll = async () => {
    markDirty()
    loadConvs()
    // Reload around the conversation in the header, without scrolling, so the approval tint and solid arrows show.
    await feed.reset(current?.first_message_id, false).catch(onError)
    if (current) get<ConversationStats>(`/conversations/${current.id}`).then(setCurrent).catch(() => {})
  }

  const doBulk = async (ids: number[]) => {
    const r = await post<BulkApproved>('/conversations/approve-bulk', { ids })
    await refreshAll()
    return r
  }

  // `live` is shared with the panel's own Undo button, so the step can only be undone once,
  // whichever of the two gets there first.
  const bulkUndo = (r: BulkApproved, live = { on: true }): Step => ({
    label: `approving ${r.approved.length.toLocaleString()} conversations`,
    run: async () => {
      if (!live.on) return null
      live.on = false
      await post('/conversations/unapprove-bulk', { ids: r.approved, accepted: r.accepted })
      await ops.current.refreshAll()
      return bulkStep(r.approved)
    }
  })
  const bulkStep = (ids: number[]): Step => ({
    label: `approving ${ids.length.toLocaleString()} conversations`,
    run: async () => bulkUndo(await ops.current.doBulk(ids))
  })

  const approveBulk = async (ids: number[]) => {
    const r = await doBulk(ids)
    const step = bulkUndo(r)
    history.record(step)
    say(`Approved ${r.approved.length.toLocaleString()} conversations. Ctrl+Z undoes it.`)
    return {
      count: r.approved.length,
      undo: async () => {
        await step.run()
      }
    }
  }

  const rejectCandidate = async (id: number) => {
    applyConv(await post<ConversationStats>(`/conversations/${id}/archive`), false)
    history.record(flagStep(id, 'unarchive'))
  }

  const openCandidate = (cand: Candidate) => {
    setFinding('hidden')
    jumpTo(cand.first_message_id, view === 'memories' ? 'threads' : view)
    get<ConversationStats>(`/conversations/${cand.id}`).then(setCurrent).catch(onError)
  }

  ops.current = { applyEdit, applyConv, doApprove, doSplit, doMerge, mergeApprovals, doBulk, refreshAll }

  const jumpTo = (messageId: string, to: View = 'threads') => {
    setView(to)
    feed.reset(messageId)
  }

  // ── keyboard ────────────────────────────────────────────────────────────
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey
      if (!mod || !project?.open) return
      const key = e.key.toLowerCase()
      const typing = (e.target as HTMLElement)?.closest('input, textarea')
      if (key === 'k') {
        e.preventDefault()
        setSearching(true)
      } else if (key === 's') {
        e.preventDefault()
        e.shiftKey ? saveAs() : save()
      } else if (key === 'o') {
        e.preventDefault()
        openProject()
      } else if (key === 'z' && !typing) {
        e.preventDefault()
        if (e.shiftKey) history.redo()
        else history.undo()
      } else if (key === 'y' && !typing) {
        e.preventDefault()
        history.redo()
      }
    }
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [project?.open, save, saveAs, history.undo, history.redo]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── active training run, for the Train button ───────────────────────────
  useEffect(() => {
    if (!project?.open) return
    let alive = true
    const poll = async () => {
      try {
        const r = await get('/train/runs')
        if (!alive) return
        if (r.active) {
          const run = await get(`/train/runs/${r.active.id}`)
          setActiveRun({ overall: run.progress?.overall ?? 0 })
        } else setActiveRun(null)
      } catch {
        /* service restarting */
      }
    }
    poll()
    const t = setInterval(poll, 4000)
    return () => {
      alive = false
      clearInterval(t)
    }
  }, [project?.open])

  // ── no project yet ──────────────────────────────────────────────────────
  if (!project)
    return (
      <div role="status" className="flex h-full items-center justify-center gap-2.5 text-sm text-muted">
        <Spinner /> Starting the curator…
      </div>
    )
  if (!project.open) {
    return (
      <div className="grid h-full place-items-center p-8">
        <div className="w-[440px] max-w-full">
          <h1 className="font-display text-3xl">Curator</h1>
          <p className="mt-2 text-sm leading-relaxed text-muted">
            Turn Discord chats into training data. A project is one .ohm file holding your imports, reply arrows, approvals and memories.
          </p>
          <div className="mt-6 flex gap-2">
            <button onClick={newProject} className="h-10 rounded-md bg-gray-100 px-4 text-sm font-semibold text-ink hover:bg-white">
              New project
            </button>
            <button onClick={openProject} className="h-10 rounded-md border border-line px-4 text-sm hover:bg-raised">
              Open project…
            </button>
          </div>
          {!!project.recent?.length && (
            <div className="mt-8">
              <div className="text-xs text-muted">Recent</div>
              {project.recent.map((r) => (
                <button
                  key={r}
                  onClick={() => openPath(r)}
                  disabled={!!busy.label}
                  className="block w-full truncate py-1.5 text-left font-mono text-[12.5px] text-gray-300 hover:text-white disabled:opacity-60"
                >
                  {r}
                </button>
              ))}
            </div>
          )}
          {error && <p className="mt-6 text-sm text-[#F28B82]">{error}</p>}
        </div>
        {recover && (
          <Dialog
            title="Restore unsaved changes?"
            body="This project has changes from last time that were never saved."
            primary="Restore them"
            secondary="Discard"
            onPrimary={() => openPath(recover, true)}
            onSecondary={() => openPath(recover, false)}
          />
        )}
        {busy.overlay}
      </div>
    )
  }

  const c = current

  return (
    // data-terms: Ctrl+click any word in here to define it (TermPicker).
    <div data-terms className="flex h-full min-h-0 flex-wrap overflow-hidden">
      {/* ── sidebar ── */}
      <nav aria-label="Curator" className="flex h-full w-[300px] max-w-full flex-col border-r border-[#2f3037] bg-ink">
        <div className="flex items-end gap-5 border-b border-[#2f3037] px-5 pt-3">
          {(
            [
              ['threads', 'Threads', LuGitBranch],
              ['raw', 'Raw', LuAlignLeft]
            ] as const
          ).map(([v, label, Icon]) => (
            <button
              key={v}
              onClick={() => setView(v)}
              aria-current={view === v ? 'page' : undefined}
              className={cx('flex h-10 items-center gap-1.5 border-b-2 text-[13.5px] font-medium', view === v ? 'border-gray-100 text-gray-100' : 'border-transparent text-muted hover:text-gray-100')}
            >
              <Icon className="size-4" />
              {label}
            </button>
          ))}
          <span className="flex-1" />
          <button
            onClick={() => setView('memories')}
            aria-label="Memories — what Ohm learned about each person"
            title="Memories"
            className={cx('flex h-10 items-center border-b-2', view === 'memories' ? 'border-gray-100 text-gray-100' : 'border-transparent text-muted hover:text-gray-100')}
          >
            <LuBrain className="size-5" />
          </button>
        </div>
        <div className="px-5 pt-5 pb-2">
          <button onClick={() => setSearching(true)} className="flex h-[38px] w-full items-center gap-2.5 rounded-md border border-line px-2.5 text-[13.5px] text-muted hover:text-gray-200">
            <LuSearch className="size-4" />
            <span className="flex-1 truncate text-left">Search…</span>
            <span className="font-mono text-[11px]">Ctrl K</span>
          </button>
        </div>
        <div className="flex flex-col gap-1.5 px-5 pt-4 pb-2.5">
          <h2 className="text-[13px] font-semibold">Conversations</h2>
          <p className="text-xs leading-relaxed text-muted">Jump points in one continuous feed, split where chat goes quiet.</p>
        </div>
        <div role="tablist" className="flex gap-4.5 border-b border-[#2f3037] px-5">
          {(['all', 'approved', 'archived'] as const).map((f) => (
            <button
              key={f}
              role="tab"
              aria-selected={filter === f}
              onClick={() => setFilter(f)}
              className={cx('h-[30px] border-b-2 text-[12.5px] capitalize', filter === f ? 'border-gray-100 text-gray-100' : 'border-transparent text-muted hover:text-gray-200')}
            >
              {f}
            </button>
          ))}
          <FilterButton filters={listFilters} onChange={changeFilters} authors={feed.authors} />
        </div>
        {filtering && !convsLoading && convs.length > 0 && (
          <div className="flex items-center gap-2 border-b border-[#2f3037] px-5 py-1.5 text-[11.5px] text-muted">
            <span className="flex-1">
              Showing {visible.length.toLocaleString()} of {convs.length.toLocaleString()}
            </span>
            <button onClick={() => changeFilters(NO_FILTERS)} className="text-gray-300 hover:text-white">
              Clear filters
            </button>
          </div>
        )}
        <VirtualRows
          items={visible}
          rowHeight={ROW_HEIGHT}
          activeIndex={activeRow}
          className="min-h-0 flex-1 overflow-auto"
          row={(cv) => (
            <button
              key={cv.id}
              onClick={() => {
                setCurrent(cv)
                jumpTo(cv.first_message_id, view === 'memories' ? 'threads' : view)
              }}
              style={{ height: ROW_HEIGHT }}
              className={cx('flex w-full flex-col justify-center gap-2 px-5 text-left', c?.id === cv.id ? 'bg-raised shadow-[inset_2px_0_0_#f3f4f6]' : 'hover:bg-[#26272d]')}
            >
              <span className="flex w-full items-center gap-2">
                <span className="flex-1 text-sm font-semibold">{fmtRange(cv.first_ts, cv.last_ts)}</span>
                {cv.approved && <LuCheck className="size-4 text-[#6CCB98]" aria-label="Approved" />}
                <span className="text-xs text-muted">{fmtDay(cv.first_ts)}</span>
              </span>
              <span className="flex items-center gap-1.5 text-xs text-muted">
                <span className="flex items-center pl-0.5">
                  {cv.skew.slice(0, 6).map((s, i) => (
                    <Avatar key={s.author_id} who={feed.authors[s.author_id]} size={16} className={i ? '-ml-1' : ''} />
                  ))}
                </span>
                <span className="ml-1">
                  {cv.people} people · {cv.messages} msgs
                </span>
              </span>
            </button>
          )}
        >
          {convsLoading && (
            <div className="flex justify-center py-6 text-muted" role="status" aria-label="Loading conversations">
              <LuLoader className="size-4 animate-spin" />
            </div>
          )}
          {!convsLoading && !convs.length && filter === 'all' && (
            <div className="px-5 py-4 text-xs text-muted">
              Nothing imported yet.
              <button onClick={importFiles} disabled={!!importing} className="mt-3 flex h-8 items-center gap-2 rounded-md border border-line px-3 text-[13px] text-gray-200 hover:bg-raised disabled:opacity-60">
                <LuFilePlus className="size-4" /> Import Discord chats…
              </button>
            </div>
          )}
          {!convsLoading && !convs.length && filter !== 'all' && <p className="px-5 py-4 text-xs text-muted">No {filter} conversations.</p>}
          {!convsLoading && convs.length > 0 && !visible.length && (
            <p className="px-5 py-4 text-xs text-muted">
              No conversations match the filters.{' '}
              <button onClick={() => changeFilters(NO_FILTERS)} className="text-gray-300 underline hover:text-white">
                Clear
              </button>
            </p>
          )}
        </VirtualRows>
        <ApprovedFooter refresh={convs} onFind={() => setFinding('open')} />
      </nav>

      {/* ── main ── */}
      <main className="flex h-full min-w-0 flex-[999_1_560px] flex-col">
        <header className="app-titlebar flex min-h-[57px] flex-wrap items-center gap-2 border-b border-[#2f3037] px-6 py-2.5">
          <button
            onClick={importFiles}
            disabled={!!importing}
            title="Import Discord exports (.json), messages.jsonl or speakers.json. Pick several at once."
            aria-label="Import file"
            className={cx(
              'flex h-9 items-center gap-2 rounded-md border border-line text-[13px] hover:bg-raised disabled:opacity-60',
              importing ? 'px-3' : 'w-9 justify-center'
            )}
          >
            {importing ? <Spinner /> : <LuFilePlus className="size-4 shrink-0" />}
            {importing && <span className="max-w-[320px] truncate text-muted">{importing}</span>}
          </button>
          <button
            onClick={toggleBots}
            aria-pressed={hideBots}
            aria-label="Hide bots"
            title={hideBots ? 'Bots are hidden. Click to show their messages again.' : 'Hide bots: leave their messages out of the feed. They are never trained on.'}
            className={cx(
              'relative grid size-9 place-items-center rounded-md border hover:bg-raised',
              hideBots ? 'border-gray-500 bg-raised text-gray-100' : 'border-line text-muted'
            )}
          >
            <LuBot className="size-4" />
            {hideBots && <span className="absolute top-1/2 left-1/2 h-[1.5px] w-5 -translate-x-1/2 -translate-y-1/2 -rotate-45 rounded bg-current" />}
          </button>
          <ModelMenu onError={onError} refreshKey={modelsKey} onEvaluate={setEvaluating} />
          {guessAll ? (
            <span className="flex h-9 items-center gap-2 rounded-md border border-line pr-1 pl-3 text-[13px]" title="The model is guessing reply arrows for every unattached block">
              <LuSparkles className="size-4 shrink-0 text-[#A78BFA]" />
              <span className="text-gray-200">{guessAll.total ? pct(guessAll.done / guessAll.total) : 'Starting…'}</span>
              {guessAll.total > 0 && (
                <span className="text-muted">
                  {guessAll.done.toLocaleString()}/{guessAll.total.toLocaleString()}
                  {guessAll.note ? ` · ${guessAll.note}` : ''}
                  {guessAll.left != null ? ` · ${fmtDuration(guessAll.left)} left` : ''}
                </span>
              )}
              <button onClick={cancelGuessAll} title="Stop guessing" aria-label="Stop guessing" className="grid size-7 place-items-center rounded hover:bg-raised">
                <LuX className="size-3.5" />
              </button>
            </span>
          ) : (
            <button
              onClick={startGuessAll}
              title="Ask the local model which message every unattached block replies to"
              className="flex h-9 items-center gap-2 rounded-md border border-line px-3 text-[13px] hover:bg-raised"
            >
              <LuSparkles className="size-4" /> Guess all
            </button>
          )}
          <span className="flex-1" />
          <span className="mr-1.5 text-xs text-muted">{project.dirty ? 'Unsaved changes' : 'Saved'}</span>
          <SaveMenu
            path={project.path || null}
            savedAt={project.saved_at || null}
            dirty={!!project.dirty}
            onSave={save}
            onSaveAs={saveAs}
            onOpen={openProject}
            onNew={newProject}
            onExport={exportJsonl}
          />
          <button onClick={() => setTraining(true)} className="h-9 rounded-md bg-gray-100 px-3.5 text-[13px] font-semibold text-ink hover:bg-white">
            {activeRun ? `Training ${pct(activeRun.overall)}` : 'Train LoRA'}
          </button>
        </header>

        {view !== 'memories' && c && (
          <section aria-label="Current conversation" className="flex flex-wrap items-center gap-x-7 gap-y-3 border-b border-[#2f3037] px-6 py-4">
            <div>
              <h1 className="text-[17px] font-semibold">
                {fmtDay(c.first_ts)} · {fmtRange(c.first_ts, c.last_ts)}
              </h1>
              <p className="mt-1 text-[12.5px] text-muted">
                {c.people} people · {c.messages} messages · {c.threads} threads ·{' '}
                <button className="underline underline-offset-2 hover:text-gray-200" onClick={() => jumpTo(c.first_message_id, view === 'raw' ? 'threads' : 'raw')}>
                  {view === 'raw' ? 'Show in Threads' : 'Show in Raw'}
                </button>
              </p>
            </div>
            {c.skew.length > 0 && (
              <div>
                <div className="text-xs text-muted">
                  Author skew · {feed.authors[c.skew[0].author_id]?.display_name || feed.authors[c.skew[0].author_id]?.name || 'top'} {pct(c.skew[0].share)}
                </div>
                <div className="mt-1.5 flex h-1 w-[220px] gap-0.5" role="img" aria-label="Messages per author">
                  {c.skew.map((s) => (
                    <span key={s.author_id} style={{ width: pct(s.share), background: color(feed.authors[s.author_id]?.color_index) }} />
                  ))}
                </div>
              </div>
            )}
            <span className="flex-1" />
            {c.unattached > 0 && <span className="text-[12.5px] text-[#E0B25C]">{c.unattached} not attached</span>}
            {c.unattached > 0 && !c.approved && (
              <button
                onClick={() => guessReplies(c)}
                disabled={!!guessing}
                title="Ask the local model which message each unattached block replies to"
                className="flex h-9 items-center gap-2 rounded-md border border-line px-3.5 text-[13px] hover:bg-raised disabled:opacity-60"
              >
                {guessing ? <Spinner /> : <LuSparkles className="size-4" />} {guessing || 'Guess with model'}
              </button>
            )}
            {c.suggested > 0 && <span className="text-[12.5px] text-muted">{c.suggested} suggested</span>}
            <button
              onClick={() => mergeUp(c)}
              title="Still the same conversation as the one before it? Join them. Clears approval; Ctrl+Z undoes it."
              aria-label="Merge with previous conversation"
              className="grid size-9 place-items-center rounded-md border border-line hover:bg-raised"
            >
              <LuMerge className="size-4" />
            </button>
            <button
              onClick={() => setFlag(c, c.archived ? 'unarchive' : 'archive')}
              title={c.archived ? 'Unarchive' : 'Archive'}
              aria-label={c.archived ? 'Unarchive conversation' : 'Archive conversation'}
              className={cx('grid size-9 place-items-center rounded-md border border-line hover:bg-raised', c.archived ? 'text-gray-100' : 'text-muted')}
            >
              <LuArchive className="size-4" />
            </button>
            {c.approved ? (
              <button
                onClick={() => setFlag(c, 'unapprove')}
                title="Take approval off. It won't be exported or trained on until you approve it again; Ctrl+Z undoes it."
                className="group/unapprove flex h-9 items-center gap-2 rounded-md border border-line px-3.5 text-[13px] hover:bg-raised"
              >
                <LuCheck className="size-4 text-[#6CCB98] group-hover/unapprove:hidden group-focus-visible/unapprove:hidden" />
                <LuX className="hidden size-4 text-bad group-hover/unapprove:block group-focus-visible/unapprove:block" />
                <span className="group-hover/unapprove:hidden group-focus-visible/unapprove:hidden">Approved</span>
                <span className="hidden group-hover/unapprove:inline group-focus-visible/unapprove:inline">Unapprove</span>
              </button>
            ) : (
              <button
                onClick={() => {
                  setApprovingId(null) // the button is replaced, so it never gets a mouseleave
                  approve(c)
                }}
                {...approveHover(c.id, setApprovingId)}
                className="h-9 rounded-md bg-gray-100 px-3.5 text-[13px] font-semibold text-ink hover:bg-white"
              >
                Approve
              </button>
            )}
          </section>
        )}

        {view !== 'memories' && (
          <div className="relative flex min-h-0 flex-1 flex-col">
            {view === 'threads' && (
              <ThreadsView
                feed={feed}
                onEdit={onEdit}
                onError={onError}
                onCurrent={setCurrent}
                onMerge={mergeUp}
                onSplit={splitAt}
                currentId={c?.id}
                approvingId={approvingId}
                onApproving={setApprovingId}
                onApprove={approve}
                onUnapprove={(conv) => setFlag(conv, 'unapprove')}
                hideBots={hideBots}
              />
            )}
            {view === 'raw' && (
              <RawView
                feed={feed}
                onSplit={splitAt}
                onMerge={mergeUp}
                onError={onError}
                openInThreads={(id) => jumpTo(id, 'threads')}
                currentId={c?.id}
                approvingId={approvingId}
                onApproving={setApprovingId}
                onApprove={approve}
                onUnapprove={(conv) => setFlag(conv, 'unapprove')}
                hideBots={hideBots}
              />
            )}
            {feed.loading && <LoadingOverlay area label="Loading messages…" />}
          </div>
        )}
        {view === 'memories' && (
          <MemoriesView selected={person} onSelect={setPerson} onDirty={markDirty} onError={onError} openMessage={(id) => jumpTo(id, 'threads')} />
        )}
      </main>

      <TermPicker onDirty={markDirty} onError={onError} />
      {searching && (
        <SearchPalette
          authors={feed.authors}
          onClose={() => setSearching(false)}
          onMessage={(id) => jumpTo(id, view === 'memories' ? 'threads' : view)}
          onPerson={(id) => {
            setPerson(id)
            setView('memories')
          }}
          onAction={(id) => {
            if (id === 'train') setTraining(true)
            else if (id === 'save') save()
            else if (id === 'export') exportJsonl()
            else if (id === 'approve' && c) approve(c)
            else if (id === 'memories' || id === 'raw' || id === 'threads') setView(id)
          }}
        />
      )}
      {training && (
        <TrainModal
          onClose={() => setTraining(false)}
          onError={onError}
          onFinished={() => {
            setModelsKey((k) => k + 1)
            setActiveRun(null)
          }}
          onEvaluate={(id) => {
            setTraining(false)
            setEvaluating(id)
          }}
        />
      )}
      {evaluating && <EvalModal adapterId={evaluating} onClose={() => setEvaluating(null)} onError={onError} />}
      {finding && (
        <FindGood
          hidden={finding === 'hidden'}
          authors={feed.authors}
          onClose={() => setFinding(null)}
          onOpen={openCandidate}
          onReject={rejectCandidate}
          onApprove={approveBulk}
          onError={onError}
        />
      )}
      {finding === 'hidden' && (
        <button
          onClick={() => setFinding('open')}
          className="fixed right-5 bottom-5 z-40 flex h-9 items-center gap-2 rounded-md border border-[#3a3b42] bg-[#1d1e22] px-3.5 text-[13px] shadow-xl hover:bg-menu-hover"
        >
          <LuWandSparkles className="size-4" /> Back to Find good conversations
        </button>
      )}
      {confirm && (
        <Dialog
          title="Approve with unattached messages?"
          body={[
            confirm.suggested ? `${confirm.suggested} suggested ${confirm.suggested === 1 ? 'reply' : 'replies'} will be accepted.` : '',
            confirm.unattached ? `${confirm.unattached} unattached ${confirm.unattached === 1 ? 'message' : 'messages'} will be left out of training.` : ''
          ]
            .filter(Boolean)
            .join(' ')}
          primary="Approve"
          secondary="Keep editing"
          onPrimary={() => approve(confirm.conv, true)}
          onSecondary={() => setConfirm(null)}
        />
      )}
      {(error || toast) && (
        <div className={cx('fixed bottom-5 left-1/2 z-50 flex max-w-[560px] -translate-x-1/2 items-start gap-3 rounded-md px-4 py-2.5 text-sm', error ? 'bg-[#3a2323] text-[#F8C4BF]' : 'bg-gray-100 text-ink')}>
          <span className="flex-1">{error || toast}</span>
          {error && (
            <button aria-label="Dismiss" onClick={() => setError(null)}>
              <LuX className="size-4" />
            </button>
          )}
        </div>
      )}
      {busy.overlay}
    </div>
  )
}

function ApprovedFooter({ refresh, onFind }: { refresh: unknown; onFind: () => void }) {
  const [counts, setCounts] = useState<{ approved: number; conversations: number } | null>(null)
  useEffect(() => {
    get('/project')
      .then((p) => setCounts(p.counts))
      .catch(() => {})
  }, [refresh])
  if (!counts) return null
  const share = counts.conversations ? counts.approved / counts.conversations : 0
  return (
    <div className="flex flex-col gap-2 border-t border-[#2f3037] px-5 pt-4 pb-5">
      <div className="flex justify-between text-xs">
        <span className="text-muted">Training set</span>
        <span className="font-mono">
          {counts.approved} / {counts.conversations} approved
        </span>
      </div>
      <div className="h-0.5 bg-[#3a3c42]">
        <div className="h-full bg-gray-100" style={{ width: pct(share) }} />
      </div>
      <p className="text-xs text-muted">Only approved conversations are exported.</p>
      <button
        onClick={onFind}
        title="Approve many good conversations at once, after skimming a sample"
        className="mt-1 flex h-8 items-center justify-center gap-2 rounded-md border border-line text-[13px] hover:bg-raised"
      >
        <LuWandSparkles className="size-4" /> Find good conversations…
      </button>
    </div>
  )
}

function Dialog(props: { title: string; body: string; primary: string; secondary: string; onPrimary: () => void; onSecondary: () => void }) {
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/55 p-4">
      <div role="alertdialog" aria-labelledby="dlg-title" className="w-[420px] max-w-full rounded-lg border border-[#3a3b42] bg-[#1d1e22] p-6">
        <h2 id="dlg-title" className="text-base font-semibold">
          {props.title}
        </h2>
        <p className="mt-2 text-sm leading-relaxed text-gray-300">{props.body}</p>
        <div className="mt-5 flex justify-end gap-2">
          <button onClick={props.onSecondary} className="h-9 rounded-md border border-line px-3.5 text-[13px] hover:bg-menu-hover">
            {props.secondary}
          </button>
          <button autoFocus onClick={props.onPrimary} className="h-9 rounded-md bg-gray-100 px-3.5 text-[13px] font-semibold text-ink hover:bg-white">
            {props.primary}
          </button>
        </div>
      </div>
    </div>
  )
}
