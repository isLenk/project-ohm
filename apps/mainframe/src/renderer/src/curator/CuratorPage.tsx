import { useCallback, useEffect, useRef, useState } from 'react'
import { LuAlignLeft, LuArchive, LuBrain, LuCheck, LuFilePlus, LuGitBranch, LuSearch, LuSparkles, LuX } from 'react-icons/lu'
import { CuratorError, call, dialog, followJob, get, post, type ConversationStats, type EditResult } from './api'
import { useFeed } from './useFeed'
import ThreadsView from './ThreadsView'
import RawView from './RawView'
import MemoriesView from './MemoriesView'
import SearchPalette from './SearchPalette'
import TrainModal from './TrainModal'
import { ModelMenu, SaveMenu } from './Menus'
import { color, cx, fmtDay, fmtRange, pct } from './util'

type View = 'threads' | 'raw' | 'memories'
type Filter = 'all' | 'approved' | 'archived'
type ProjectInfo = { open: boolean; path?: string; name?: string; dirty?: boolean; saved_at?: number | null; recent?: string[] }

const OHM_FILTER = [{ name: 'Ohm project', extensions: ['ohm'] }]

export default function CuratorPage() {
  const feed = useFeed()
  const [project, setProject] = useState<ProjectInfo | null>(null)
  const [view, setView] = useState<View>('threads')
  const [filter, setFilter] = useState<Filter>('all')
  const [convs, setConvs] = useState<ConversationStats[]>([])
  const [current, setCurrent] = useState<ConversationStats | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const [searching, setSearching] = useState(false)
  const [training, setTraining] = useState(false)
  const [activeRun, setActiveRun] = useState<{ overall: number } | null>(null)
  const [confirm, setConfirm] = useState<{ conv: ConversationStats; suggested: number; unattached: number } | null>(null)
  const [person, setPerson] = useState<string | null>(null)
  const [modelsKey, setModelsKey] = useState(0)
  const [recover, setRecover] = useState<string | null>(null)
  const [guessing, setGuessing] = useState<string | null>(null)
  const undo = useRef<EditResult['inverse'][]>([])

  const onError = useCallback((e: unknown) => {
    const msg = e instanceof Error ? e.message : String(e)
    setError(msg)
  }, [])

  const say = (msg: string) => {
    setToast(msg)
    setTimeout(() => setToast((t) => (t === msg ? null : t)), 3500)
  }

  const markDirty = useCallback(() => setProject((p) => (p ? { ...p, dirty: true } : p)), [])

  const loadConvs = useCallback(async () => {
    try {
      const r = await get<{ conversations: ConversationStats[] }>(`/conversations?filter=${filter}`)
      setConvs(r.conversations)
    } catch (e) {
      if (!(e instanceof CuratorError && e.code === 'no_project')) onError(e)
    }
  }, [filter, onError])

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
        feed.reset()
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
  const opened = (p: any) => {
    setProject({ open: true, ...p })
    undo.current = []
    feed.reset()
    loadConvs()
  }

  const openPath = async (path: string, recoverChoice?: boolean) => {
    try {
      opened(await post('/project/open', { path, recover: recoverChoice ?? null }))
      setRecover(null)
    } catch (e) {
      if (e instanceof CuratorError && e.code === 'recovery_available') setRecover(path)
      else onError(e)
    }
  }

  const openProject = async () => {
    const r = await dialog('showOpenDialog', { properties: ['openFile'], filters: OHM_FILTER })
    if (!r.canceled && r.filePaths?.[0]) openPath(r.filePaths[0])
  }

  const newProject = async () => {
    const r = await dialog('showSaveDialog', { title: 'New Curator project', defaultPath: 'conversations.ohm', filters: OHM_FILTER })
    if (r.canceled || !r.filePath) return
    try {
      opened(await post('/project/new', { path: r.filePath }))
    } catch (e) {
      onError(e)
    }
  }

  const save = useCallback(async () => {
    try {
      const r = await post('/project/save')
      setProject((p) => (p ? { ...p, dirty: false, saved_at: r.saved_at } : p))
      say('Saved')
    } catch (e) {
      onError(e)
    }
  }, [onError])

  const saveAs = useCallback(async () => {
    const r = await dialog('showSaveDialog', { title: 'Save project to file', defaultPath: project?.path, filters: OHM_FILTER })
    if (r.canceled || !r.filePath) return
    try {
      const s = await post('/project/save-as', { path: r.filePath })
      setProject((p) => (p ? { ...p, dirty: false, saved_at: s.saved_at, path: s.path } : p))
      say('Saved')
    } catch (e) {
      onError(e)
    }
  }, [project?.path, onError])

  const exportJsonl = async () => {
    const r = await dialog('showSaveDialog', { title: 'Export approved conversations', defaultPath: 'training.jsonl', filters: [{ name: 'JSONL', extensions: ['jsonl'] }] })
    if (r.canceled || !r.filePath) return
    try {
      const out = await post('/export/jsonl', { path: r.filePath })
      say(`Exported ${out.examples} examples`)
    } catch (e) {
      onError(e)
    }
  }

  // ── edits & undo ────────────────────────────────────────────────────────
  const onEdit = useCallback(
    (r: EditResult) => {
      feed.apply(r)
      if (r.inverse) undo.current.push(r.inverse)
      markDirty()
      patchConvs(r.conversations_changed || [])
      if (current && r.conversations_changed?.some((c) => c.id === current.id)) {
        get<ConversationStats>(`/conversations/${current.id}`).then((c) => {
          setCurrent(c)
          feed.updateConversation(c)
          patchConvs([c])
        })
      } else if (current) {
        get<ConversationStats>(`/conversations/${current.id}`)
          .then((c) => {
            setCurrent(c)
            patchConvs([c])
          })
          .catch(() => {})
      }
    },
    [feed, markDirty, patchConvs, current]
  )

  const doUndo = useCallback(async () => {
    const inv = undo.current.pop()
    if (!inv) return
    try {
      const r = await call<EditResult>(inv.method, inv.path, inv.body)
      feed.apply(r)
      markDirty()
      patchConvs(r.conversations_changed || [])
      if (current) {
        get<ConversationStats>(`/conversations/${current.id}`)
          .then((c) => {
            setCurrent(c)
            patchConvs([c])
          })
          .catch(() => {})
      }
      say('Undone')
    } catch (e) {
      onError(e)
    }
  }, [feed, markDirty, patchConvs, current, onError])

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
  const approve = async (c: ConversationStats, confirmed = false) => {
    try {
      const s = await post<ConversationStats>(`/conversations/${c.id}/approve`, { confirm: confirmed })
      setConfirm(null)
      setCurrent(s)
      feed.updateConversation(s)
      markDirty()
      patchConvs([s])
      feed.reset(c.first_message_id, false) // suggested arrows just became solid
    } catch (e) {
      if (e instanceof CuratorError && e.code === 'needs_confirm') setConfirm({ conv: c, suggested: e.extra.suggested, unattached: e.extra.unattached })
      else onError(e)
    }
  }

  const setFlag = async (c: ConversationStats, path: string) => {
    try {
      const s = await post<ConversationStats>(`/conversations/${c.id}/${path}`)
      setCurrent(s)
      feed.updateConversation(s)
      markDirty()
      patchConvs([s])
    } catch (e) {
      onError(e)
    }
  }

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
        doUndo()
      }
    }
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [project?.open, save, saveAs, doUndo]) // eslint-disable-line react-hooks/exhaustive-deps

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
  if (!project) return <div className="grid h-full place-items-center text-sm text-muted">Starting the curator…</div>
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
                <button key={r} onClick={() => openPath(r)} className="block w-full truncate py-1.5 text-left font-mono text-[12.5px] text-gray-300 hover:text-white">
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
      </div>
    )
  }

  const c = current

  return (
    <div className="flex h-full min-h-0 flex-wrap overflow-hidden">
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
        </div>
        <div className="min-h-0 flex-1 overflow-auto py-1.5">
          {convs.map((cv) => (
            <button
              key={cv.id}
              onClick={() => jumpTo(cv.first_message_id, view === 'memories' ? 'threads' : view)}
              className={cx('flex w-full flex-col gap-2 px-5 py-3.5 text-left [contain-intrinsic-size:auto_72px] [content-visibility:auto]', c?.id === cv.id ? 'bg-raised shadow-[inset_2px_0_0_#f3f4f6]' : 'hover:bg-[#26272d]')}
            >
              <span className="flex w-full items-center gap-2">
                <span className="flex-1 text-sm font-semibold">{fmtRange(cv.first_ts, cv.last_ts)}</span>
                {cv.approved && <LuCheck className="size-4 text-[#6CCB98]" aria-label="Approved" />}
                <span className="text-xs text-muted">{fmtDay(cv.first_ts)}</span>
              </span>
              <span className="flex items-center gap-1.5 text-xs text-muted">
                {cv.skew.slice(0, 6).map((s) => (
                  <span key={s.author_id} className="size-2 rounded-full" style={{ background: color(feed.authors[s.author_id]?.color_index) }} />
                ))}
                <span className="ml-1">
                  {cv.people} people · {cv.messages} msgs
                </span>
              </span>
            </button>
          ))}
          {!convs.length && <p className="px-5 py-4 text-xs text-muted">{filter === 'all' ? 'Nothing imported yet.' : `No ${filter} conversations.`}</p>}
        </div>
        <ApprovedFooter refresh={convs} />
      </nav>

      {/* ── main ── */}
      <main className="flex h-full min-w-0 flex-[999_1_560px] flex-col">
        <header className="flex min-h-[57px] flex-wrap items-center gap-2 border-b border-[#2f3037] px-6">
          <button disabled title="Import isn't wired up yet" aria-label="Import file" className="grid size-9 place-items-center rounded-md border border-line text-muted opacity-50">
            <LuFilePlus className="size-4" />
          </button>
          <ModelMenu onError={onError} refreshKey={modelsKey} />
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
                <LuSparkles className="size-4" /> {guessing || 'Guess with model'}
              </button>
            )}
            {c.suggested > 0 && <span className="text-[12.5px] text-muted">{c.suggested} suggested</span>}
            <button
              onClick={() => setFlag(c, c.archived ? 'unarchive' : 'archive')}
              title={c.archived ? 'Unarchive' : 'Archive'}
              aria-label={c.archived ? 'Unarchive conversation' : 'Archive conversation'}
              className={cx('grid size-9 place-items-center rounded-md border border-line hover:bg-raised', c.archived ? 'text-gray-100' : 'text-muted')}
            >
              <LuArchive className="size-4" />
            </button>
            {c.approved ? (
              <button onClick={() => setFlag(c, 'unapprove')} className="flex h-9 items-center gap-2 rounded-md border border-line px-3.5 text-[13px] hover:bg-raised">
                <LuCheck className="size-4 text-[#6CCB98]" /> Approved
              </button>
            ) : (
              <button onClick={() => approve(c)} className="h-9 rounded-md bg-gray-100 px-3.5 text-[13px] font-semibold text-ink hover:bg-white">
                Approve
              </button>
            )}
          </section>
        )}

        {view === 'threads' && <ThreadsView feed={feed} onEdit={onEdit} onError={onError} onCurrent={setCurrent} />}
        {view === 'raw' && <RawView feed={feed} onChanged={() => (markDirty(), loadConvs())} onError={onError} openInThreads={(id) => jumpTo(id, 'threads')} />}
        {view === 'memories' && (
          <MemoriesView selected={person} onSelect={setPerson} onDirty={markDirty} onError={onError} openMessage={(id) => jumpTo(id, 'threads')} />
        )}
      </main>

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
        />
      )}
      {confirm && (
        <Dialog
          title="Approve with replies still open?"
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
    </div>
  )
}

function ApprovedFooter({ refresh }: { refresh: unknown }) {
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
          <button onClick={props.onSecondary} className="h-9 rounded-md border border-line px-3.5 text-[13px] hover:bg-raised">
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
