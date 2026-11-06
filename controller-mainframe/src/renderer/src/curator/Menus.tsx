import { useCallback, useEffect, useRef, useState } from 'react'
import { LuCheck, LuChevronDown, LuPlus, LuSave } from 'react-icons/lu'
import { CuratorError, dialog, followJob, get, patch, post, put } from './api'
import { cx } from './util'

function useOutside(open: boolean, close: () => void) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) close()
    }
    const k = (e: KeyboardEvent) => e.key === 'Escape' && close()
    window.addEventListener('mousedown', h)
    window.addEventListener('keydown', k)
    return () => {
      window.removeEventListener('mousedown', h)
      window.removeEventListener('keydown', k)
    }
  }, [open, close])
  return ref
}

// ── model dropdown ──────────────────────────────────────────────────────────
type BaseModel = { id: string; name: string; gguf_path: string; quant: string | null; hf_path: string | null; different_base: number }
type Adapter = { id: string; base_model_id: string; name: string; gguf_path: string; created_at: number }
type Models = {
  base_models: BaseModel[]
  adapters: Adapter[]
  active: { base_model_id: string | null; adapter_id: string | null }
  llm: { running: boolean; model_path: string | null }
}

export function ModelMenu({ onError, refreshKey }: { onError: (e: unknown) => void; refreshKey: number }) {
  const [open, setOpen] = useState(false)
  const [m, setM] = useState<Models | null>(null)
  const [hf, setHf] = useState<string | null>(null)
  const [hfFiles, setHfFiles] = useState<string[]>([])
  const [weights, setWeights] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [restart, setRestart] = useState(false)
  const close = useCallback(() => setOpen(false), [])
  const ref = useOutside(open, close)

  const load = useCallback(async () => {
    try {
      setM(await get<Models>('/models'))
    } catch (e) {
      onError(e)
    }
  }, [onError])

  useEffect(() => {
    load()
  }, [load, refreshKey])

  const base = m?.base_models.find((b) => b.id === m.active.base_model_id)
  const adapter = m?.adapters.find((a) => a.id === m.active.adapter_id)
  const adapters = m?.adapters.filter((a) => a.base_model_id === base?.id) || []

  const activate = async (base_model_id: string, adapter_id: string | null) => {
    try {
      const r = await put('/models/active', { base_model_id, adapter_id })
      setM(r)
      setRestart(r.needs_restart)
      setStatus(r.applied ? 'Applied to the running llama-server.' : r.needs_restart ? null : 'llama-server is not running; this applies next time it starts.')
    } catch (e) {
      onError(e)
    }
  }

  const doRestart = async () => {
    try {
      const j = await post('/llm/restart')
      setStatus('Restarting llama-server…')
      await followJob(j.id, (p) => setStatus(p.note))
      setRestart(false)
      setStatus('llama-server restarted with the adapter loaded.')
      load()
    } catch (e) {
      onError(e)
    }
  }

  const importFile = async (kind: 'base' | 'adapter') => {
    const r = await dialog('showOpenDialog', { properties: ['openFile'], filters: [{ name: 'GGUF', extensions: ['gguf'] }] })
    if (r.canceled || !r.filePaths?.[0]) return
    try {
      if (kind === 'base') setM(await post('/models/base', { gguf_path: r.filePaths[0] }))
      else if (base) setM(await post('/models/adapters', { path: r.filePaths[0], base_model_id: base.id }))
    } catch (e) {
      onError(e)
    }
  }

  const fromHf = async (filename?: string) => {
    if (!hf?.trim()) return
    try {
      const j = await post('/models/base/hf', { repo_id: hf.trim(), filename })
      setHfFiles([])
      setStatus('Downloading…')
      await followJob(j.id, (p) => setStatus(p.note))
      setHf(null)
      setStatus('Downloaded.')
      load()
    } catch (e) {
      if (e instanceof CuratorError && e.code === 'choose_file') setHfFiles(e.extra.files)
      else onError(e)
    }
  }

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className={cx('flex h-9 min-w-[280px] items-center gap-2 rounded-md border px-3 text-[13px]', open ? 'border-gray-500 bg-raised' : 'border-line hover:bg-raised')}
      >
        <span className="flex-1 truncate text-left">{base ? base.name : 'No model'}</span>
        <span className="truncate font-mono text-[11px] text-muted">
          {base?.quant} · {adapter ? adapter.name : 'no adapter'}
        </span>
        <LuChevronDown className="size-4 text-muted" />
      </button>
      {open && m && (
        <div role="listbox" className="absolute top-11 left-0 z-40 w-[460px] rounded-md border border-[#3a3b42] bg-[#1d1e22] py-1 text-sm shadow-none">
          <div className="px-3.5 pt-2.5 pb-1.5 text-[11.5px] text-muted">Base models you’ve imported</div>
          {m.base_models.length === 0 && <div className="px-3.5 py-2 text-xs text-muted">None yet.</div>}
          {m.base_models.map((b) => (
            <button
              key={b.id}
              role="option"
              aria-selected={b.id === base?.id}
              onClick={() => activate(b.id, null)}
              className={cx('flex w-full items-center gap-3 px-3.5 py-2 text-left hover:bg-raised', b.id === base?.id && 'bg-[#26272d]')}
            >
              <span className="min-w-0 flex-1">
                <span className="block font-medium">{b.name}</span>
                <span className="block truncate font-mono text-[11px] text-muted">{b.gguf_path.split(/[\\/]/).pop()}</span>
              </span>
              {b.id === base?.id && <LuCheck className="size-4" />}
            </button>
          ))}
          {base && (
            <div className="px-3.5 py-2 text-xs text-muted">
              Trains on{' '}
              {weights !== null ? (
                <form
                  className="mt-1 flex gap-2"
                  onSubmit={async (e) => {
                    e.preventDefault()
                    try {
                      setM(await patch(`/models/base/${base.id}`, { hf_path: weights }))
                      setWeights(null)
                    } catch (err) {
                      onError(err)
                    }
                  }}
                >
                  <input autoFocus value={weights} onChange={(e) => setWeights(e.target.value)} placeholder="HF repo id or local folder" className="h-7 flex-1 rounded border border-line bg-transparent px-2 font-mono text-[11px] text-gray-100 outline-none" />
                  <button className="text-gray-200">Save</button>
                </form>
              ) : (
                <>
                  <span className="font-mono text-gray-300">{base.hf_path || 'nothing yet'}</span>{' '}
                  <button className="underline underline-offset-2 hover:text-gray-200" onClick={() => setWeights(base.hf_path || '')}>
                    change
                  </button>
                  {base.different_base ? <div className="mt-1 text-[#E0B25C]">Adapters trained on these weights run on a slightly different model. Compare a few replies.</div> : null}
                </>
              )}
            </div>
          )}
          <div className="my-1 h-px bg-[#2f3037]" />
          <div className="px-3.5 pt-2 pb-1.5 text-[11.5px] text-muted">LoRA adapters you’ve trained</div>
          {base && (
            <button onClick={() => activate(base.id, null)} className="flex w-full items-center px-3.5 py-2 text-left hover:bg-raised">
              <span className="flex-1">No adapter</span>
              {!adapter && <LuCheck className="size-4" />}
            </button>
          )}
          {adapters.map((a) => (
            <button key={a.id} onClick={() => activate(a.base_model_id, a.id)} className="flex w-full items-center px-3.5 py-2 text-left hover:bg-raised">
              <span className="flex-1">{a.name}</span>
              {a.id === adapter?.id && <LuCheck className="size-4" />}
            </button>
          ))}
          {!adapters.length && <div className="px-3.5 pb-2 text-xs leading-relaxed text-muted">None yet. Each finished training run lands here.</div>}
          {restart && (
            <div className="mx-3.5 my-2 flex items-center gap-2 text-xs text-[#E0B25C]">
              <span className="flex-1">llama-server needs a restart to load this.</span>
              <button onClick={doRestart} className="rounded border border-line px-2 py-1 text-gray-100 hover:bg-raised">
                Restart it
              </button>
            </div>
          )}
          {status && <div className="px-3.5 py-1.5 text-xs text-muted">{status}</div>}
          <div className="my-1 h-px bg-[#2f3037]" />
          <div className="px-3.5 pt-2 pb-1 text-[11.5px] text-muted">Add new</div>
          <button onClick={() => importFile('base')} className="flex w-full items-center gap-3 px-3.5 py-2 text-left hover:bg-raised">
            <LuPlus className="size-4" /> Import a .gguf model…
          </button>
          <button onClick={() => importFile('adapter')} disabled={!base} className="flex w-full items-center gap-3 px-3.5 py-2 text-left hover:bg-raised disabled:opacity-50">
            <LuPlus className="size-4" /> Import a LoRA adapter…
          </button>
          {hf === null ? (
            <button onClick={() => setHf('')} className="flex w-full items-center gap-3 px-3.5 py-2 text-left hover:bg-raised">
              <LuPlus className="size-4" /> From Hugging Face…
            </button>
          ) : (
            <form
              className="flex gap-2 px-3.5 py-2"
              onSubmit={(e) => {
                e.preventDefault()
                fromHf()
              }}
            >
              <input autoFocus value={hf} onChange={(e) => setHf(e.target.value)} placeholder="owner/repo" className="h-8 flex-1 rounded border border-line bg-transparent px-2 font-mono text-xs outline-none" />
              <button className="rounded border border-line px-2 text-xs hover:bg-raised">Fetch</button>
            </form>
          )}
          {hfFiles.map((f) => (
            <button key={f} onClick={() => fromHf(f)} className="block w-full truncate px-6 py-1.5 text-left font-mono text-[11px] text-gray-300 hover:bg-raised">
              {f}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ── save menu ───────────────────────────────────────────────────────────────
type SaveProps = {
  path: string | null
  savedAt: number | null
  dirty: boolean
  onSave: () => void
  onSaveAs: () => void
  onOpen: () => void
  onNew: () => void
  onExport: () => void
}

export function SaveMenu({ path, savedAt, dirty, onSave, onSaveAs, onOpen, onNew, onExport }: SaveProps) {
  const [open, setOpen] = useState(false)
  const close = useCallback(() => setOpen(false), [])
  const ref = useOutside(open, close)
  const item = (label: string, sub: string | null, keys: string | null, fn: () => void) => (
    <button
      role="menuitem"
      onClick={() => {
        close()
        fn()
      }}
      className="flex w-full items-center gap-3 px-3.5 py-2.5 text-left text-[13.5px] hover:bg-raised"
    >
      <span className="flex-1">
        {label}
        {sub && <span className="mt-0.5 block text-xs text-muted">{sub}</span>}
      </span>
      {keys && <span className="font-mono text-[11.5px] text-muted">{keys}</span>}
    </button>
  )
  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-label="Save"
        title="Save"
        aria-haspopup="menu"
        className={cx('flex h-9 items-center gap-1 rounded-md border px-2', open ? 'border-gray-500 bg-raised' : 'border-line hover:bg-raised')}
      >
        <LuSave className="size-4" />
        <LuChevronDown className="size-3.5 text-muted" />
      </button>
      {open && (
        <div role="menu" className="absolute top-11 right-0 z-40 w-[330px] rounded-md border border-[#3a3b42] bg-[#1d1e22] py-1.5">
          <div className="px-3.5 pt-1.5 pb-2.5">
            <div className="text-xs text-muted">Project file</div>
            <div className="mt-1 truncate font-mono text-[12.5px] text-gray-300">{path?.split(/[\\/]/).pop()}</div>
            <div className="mt-1 text-xs text-muted">
              {dirty ? 'Unsaved changes' : savedAt ? `Saved ${new Date(savedAt).toLocaleTimeString()}` : 'Not saved yet'}
            </div>
          </div>
          <div className="h-px bg-[#2f3037]" />
          {item('Save', null, 'Ctrl S', onSave)}
          {item('Save to file…', 'Arrows, splits, approvals and memories in one .ohm file', 'Ctrl ⇧ S', onSaveAs)}
          {item('Open project…', null, 'Ctrl O', onOpen)}
          {item('New project…', null, null, onNew)}
          <div className="my-1.5 h-px bg-[#2f3037]" />
          {item('Export approved as JSONL…', 'Just the training examples, no project state', null, onExport)}
          <div className="my-1.5 h-px bg-[#2f3037]" />
          <div aria-disabled className="flex items-center gap-3 px-3.5 py-2.5 text-[13.5px] text-muted">
            <span className="flex-1">
              Autosave<span className="mt-0.5 block text-xs">Coming later</span>
            </span>
            <span className="relative h-4 w-[30px] rounded-full bg-[#2f3037]">
              <span className="absolute top-0.5 left-0.5 size-3 rounded-full bg-[#4a4d55]" />
            </span>
          </div>
        </div>
      )}
    </div>
  )
}
