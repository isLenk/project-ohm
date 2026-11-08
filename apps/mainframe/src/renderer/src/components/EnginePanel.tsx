import { useState } from 'react'
import { Link } from 'react-router-dom'
import { LuDownload, LuPlay, LuRotateCcw, LuSquare, LuUpload } from 'react-icons/lu'
import { followJob, get, post } from '../curator/api'
import { message, moduleName, since, useEngine, usePoll } from '../lib/ohm'
import { Spinner } from './Loader'

/* The two halves of the engine, which are separate processes today:
   the model (llama-server, through curator_service) and the Python side (main.py, run by the app). */
const EnginePanel = ({ llm: first, onClose }) => {
  const engine = useEngine()
  const { data: polled } = usePoll(() => get('/llm/status'), 3000)
  const { data: models } = usePoll(() => get('/models'), 15000)
  const { data: modules } = usePoll(() => window.api.listModules(), 5000)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const llm = polled ?? first

  const base = models?.base_models.find((m) => m.id === llm?.active?.base_model_id)
  const adapter = models?.adapters.find((a) => a.id === llm?.active?.adapter_id)
  const running = (modules ?? []).filter((m) => m.enabled).map((m) => moduleName(m.name))

  const run = async (label: string, fn: () => Promise<any>) => {
    setBusy(label)
    setError(null)
    try {
      await fn()
    } catch (e) {
      setError(message(e))
    } finally {
      setBusy(null)
    }
  }
  // Load and Reload are background jobs in the curator service
  const job = (path: string) => async () => {
    const j = await post(path)
    const result = await followJob(
      j.id,
      (p) => p.note && setBusy(`${p.note[0].toUpperCase()}${p.note.slice(1)}…`)
    )
    if (result && !result.ok)
      throw new Error('llama-server did not come up. Check its window for errors.')
  }
  const restartAll = async () => {
    await window.api.stopEngine()
    if (llm?.running) await job('/llm/restart')()
    else await job('/llm/start')()
    await window.api.startEngine()
  }

  const model = llm?.ready
    ? ['Loaded', 'bg-good']
    : llm?.running
      ? ['Starting…', 'bg-warn']
      : ['Not loaded', 'bg-[#4a4d55]']
  const py = {
    running: ['Running', 'bg-good'],
    starting: ['Starting…', 'bg-warn'],
    stopping: ['Stopping…', 'bg-warn'],
    stopped: ['Stopped', 'bg-[#4a4d55]']
  }[engine?.state ?? 'stopped']
  const vram = llm?.vram?.used_gb && llm.vram.total_gb ? llm.vram.used_gb / llm.vram.total_gb : null

  return (
    <>
      <div className="fixed inset-0 z-40" onClick={onClose} />
      <div
        role="dialog"
        aria-label="Engine"
        onKeyDown={(e) => e.key === 'Escape' && onClose()}
        className="absolute bottom-5 left-[calc(100%+10px)] z-50 w-100 overflow-hidden rounded-lg border border-edge bg-panel text-sm shadow-2xl"
      >
        <div className="flex items-center gap-2.5 border-b border-line px-4 py-3">
          <h2 className="flex-1 text-sm font-semibold">Engine</h2>
          {busy && (
            <span role="status" className="flex items-center gap-1.5 text-xs text-warn">
              <Spinner className="size-3.5" />
              {busy}
            </span>
          )}
        </div>

        <section className="flex flex-col gap-2.5 border-b border-line px-4 py-3.5">
          <div className="flex items-center gap-2 text-[13px] font-semibold">
            Model
            <span className="flex-1" />
            <Status text={model[0]} dot={model[1]} />
          </div>
          <dl className="grid grid-cols-[84px_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-[12.5px]">
            <dt className="text-muted">Base</dt>
            <dd className="truncate">
              {base?.name ?? 'None picked'}{' '}
              {base?.quant && (
                <span className="font-mono text-[11px] text-muted">{base.quant}</span>
              )}
            </dd>
            <dt className="text-muted">LoRA</dt>
            <dd className="truncate">{adapter?.name ?? 'None'}</dd>
            {vram != null && (
              <>
                <dt className="text-muted">VRAM</dt>
                <dd className="flex items-center gap-2.5">
                  <span className="h-1.5 flex-1 rounded-full bg-line">
                    <span
                      className="block h-1.5 rounded-full bg-clay"
                      style={{ width: `${vram * 100}%` }}
                    />
                  </span>
                  <span className="font-mono text-[11px] text-soft">
                    {llm.vram.used_gb.toFixed(1)} / {llm.vram.total_gb.toFixed(0)} GB
                  </span>
                </dd>
              </>
            )}
            {llm?.uptime_s != null && (
              <>
                <dt className="text-muted">Up for</dt>
                <dd className="font-mono text-xs">{since(Date.now() - llm.uptime_s * 1000)}</dd>
              </>
            )}
          </dl>
          <div className="flex flex-wrap gap-1.5">
            {llm?.running ? (
              <>
                <button
                  className="btn"
                  disabled={!!busy}
                  onClick={() => run('Reloading…', job('/llm/restart'))}
                >
                  <LuRotateCcw /> Reload
                </button>
                <button
                  className="btn"
                  disabled={!!busy}
                  onClick={() => run('Unloading…', () => post('/llm/stop'))}
                >
                  <LuUpload /> Unload
                </button>
              </>
            ) : (
              <button
                className="btn btn-primary"
                disabled={!!busy}
                onClick={() => run('Loading…', job('/llm/start'))}
              >
                <LuDownload /> Load
              </button>
            )}
          </div>
        </section>

        <section className="flex flex-col gap-2.5 border-b border-line px-4 py-3.5">
          <div className="flex items-center gap-2 text-[13px] font-semibold">
            Python side{' '}
            <span className="font-mono text-[11px] font-normal text-muted">main.py</span>
            <span className="flex-1" />
            <Status text={py[0]} dot={py[1]} />
          </div>
          <dl className="grid grid-cols-[84px_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-[12.5px]">
            <dt className="text-muted">Runs</dt>
            <dd>{running.join(', ') || '—'}</dd>
            <dt className="text-muted">Up for</dt>
            <dd className="font-mono text-xs">
              {engine?.state === 'running' ? since(engine.startedAt) : '—'}
            </dd>
          </dl>
          {engine?.external && (
            <p className="text-xs leading-relaxed text-warn">
              Started outside the app. _run_script.cmd starts it again after Stop; close that window
              to stop it for good.
            </p>
          )}
          <div className="flex flex-wrap gap-1.5">
            {engine?.state === 'stopped' ? (
              <button
                className="btn btn-primary"
                disabled={!!busy}
                onClick={() => run('Starting…', window.api.startEngine)}
              >
                <LuPlay /> Start
              </button>
            ) : (
              <>
                <button
                  className="btn"
                  disabled={!!busy || engine?.state !== 'running'}
                  onClick={() => run('Restarting…', window.api.restartEngine)}
                >
                  <LuRotateCcw /> Restart
                </button>
                <button
                  className="btn border-[#4a2a2a] text-bad"
                  disabled={!!busy || engine?.state === 'stopping'}
                  onClick={() => run('Stopping…', window.api.stopEngine)}
                >
                  <LuSquare /> Stop
                </button>
              </>
            )}
          </div>
        </section>

        {error && <p className="border-b border-line px-4 py-2.5 text-xs text-bad">{error}</p>}

        <div className="flex items-center gap-3 px-4 py-2.5">
          <button
            className="btn"
            disabled={!!busy}
            onClick={() => run('Restarting everything…', restartAll)}
          >
            Restart everything
          </button>
          <span className="flex-1" />
          <Link to="/" onClick={onClose} className="text-xs text-soft hover:text-gray-100">
            Logs
          </Link>
          <Link to="/settings" onClick={onClose} className="text-xs text-soft hover:text-gray-100">
            Runtime settings
          </Link>
        </div>
        <p className="px-4 pb-3 text-[11.5px] leading-relaxed text-muted">
          Playing a different character card switches the model and restarts the Python side as
          that character. Stopping it takes them offline in Discord.
        </p>
      </div>
    </>
  )
}

const Status = ({ text, dot }) => (
  <span className="inline-flex items-center gap-1.5 text-xs font-normal text-soft">
    <span className={`size-2 rounded-full ${dot}`} />
    {text}
  </span>
)

export default EnginePanel
