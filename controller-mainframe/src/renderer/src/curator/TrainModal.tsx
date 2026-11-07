import { useEffect, useMemo, useState } from 'react'
import { LuCheck, LuLoader, LuX } from 'react-icons/lu'
import { get, post, stream } from './api'
import { cx, fmtDuration, pct } from './util'

const STAGES = [
  ['export', 'Export approved threads to examples'],
  ['tokenize', 'Tokenize'],
  ['train', 'Train'],
  ['save', 'Save adapter and add it to the model list']
] as const

type Preflight = {
  base_model: { name: string; hf_path: string | null } | null
  trainable: boolean
  simulate: boolean
  export: {
    examples: number
    conversations: number
    by_author: { name: string; share: number; count: number }[]
    after_cap: { name: string; share: number; count: number }[]
    dropped: Record<string, number>
  }
  gpu: { name: string; total_gb: number; free_gb: number; processes: { pid: number; name: string; used_gb: number }[] } | null
  need_gb: number
  enough_vram: boolean
  active_run: { id: string } | null
}

type Run = {
  id: string
  status: string
  started_at: number
  finished_at: number | null
  config: any
  error: string | null
  adapter_id: string | null
  example_count: number
  stopped_services: { name: string; is_llama: boolean }[]
  progress: any
  losses: { step: number; loss: number }[]
}

type Props = { onClose: () => void; onError: (e: unknown) => void; onFinished: () => void }

export default function TrainModal({ onClose, onError, onFinished }: Props) {
  const [pre, setPre] = useState<Preflight | null>(null)
  const [run, setRun] = useState<Run | null>(null)
  const [progress, setProgress] = useState<any>(null)
  const [stageTimes, setStageTimes] = useState<Record<string, number>>({})
  const [losses, setLosses] = useState<{ step: number; loss: number }[]>([])
  const [stop, setStop] = useState<number[]>([])
  const [ended, setEnded] = useState<{ status: string; error?: string } | null>(null)
  const [now, setNow] = useState(Date.now())
  const [starting, setStarting] = useState(false)

  // Attach to a running run, or show the preflight.
  useEffect(() => {
    ;(async () => {
      try {
        const runs = await get<{ active: Run | null }>('/train/runs')
        if (runs.active) setRun(await get<Run>(`/train/runs/${runs.active.id}`))
        else {
          const p = await get<Preflight>('/train/preflight')
          setPre(p)
          if (p.gpu && !p.enough_vram) setStop(p.gpu.processes.map((x) => x.pid))
        }
      } catch (e) {
        onError(e)
      }
    })()
  }, [onError])

  useEffect(() => {
    if (!run) return
    setLosses(run.losses)
    setProgress(run.progress)
    const close = stream(`/train/runs/${run.id}/events`, (name, d) => {
      if (name === 'progress') {
        setProgress(d)
        if (d.loss != null) setLosses((l) => (l.length && l[l.length - 1].step === d.step ? l : [...l, { step: d.step, loss: d.loss }]))
      } else if (name === 'stage') setStageTimes((s) => ({ ...s, [d.stage]: d.elapsed_s }))
      else if (name === 'end') {
        setEnded(d)
        onFinished()
      } else if (name === 'error') setEnded({ status: 'failed', error: d.error })
    })
    return close
  }, [run?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!run || ended) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [run, ended])

  const start = async () => {
    setStarting(true)
    try {
      setRun(await post<Run>('/train', { stop_pids: stop }))
    } catch (e) {
      onError(e)
    } finally {
      setStarting(false)
    }
  }

  const cancel = async () => {
    if (!run) return
    try {
      await post(`/train/runs/${run.id}/cancel`)
    } catch (e) {
      onError(e)
    }
  }

  const elapsed = run ? (ended ? progress?.elapsed_s : (now - run.started_at) / 1000) : 0
  const stageIndex = progress?.stage_index ?? 0
  const overall = ended?.status === 'succeeded' ? 1 : progress?.overall ?? 0

  const spark = useMemo(() => {
    if (losses.length < 2) return null
    const w = 360
    const h = 56
    const max = Math.max(...losses.map((l) => l.loss))
    const min = Math.min(...losses.map((l) => l.loss))
    const total = progress?.total_steps || losses[losses.length - 1].step
    const pts = losses.map((l) => `${(l.step / total) * w},${4 + (1 - (l.loss - min) / (max - min || 1)) * (h - 8)}`)
    return { d: `M${pts.join(' L')}`, w, h }
  }, [losses, progress?.total_steps])

  const statusIcon = (i: number, key: string) => {
    const doneStage = ended?.status === 'succeeded' || i < stageIndex
    if (doneStage) return <LuCheck className="size-4 text-[#6CCB98]" />
    if (ended && i >= stageIndex) return <span className="size-4 rounded-full border-2 border-[#3a3c42]" />
    if (i === stageIndex) return <LuLoader className="size-4 animate-spin" />
    return <span className="size-4 rounded-full border-2 border-[#3a3c42]" aria-label={key} />
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/55 p-4">
      <div role="dialog" aria-labelledby="train-title" className="w-[600px] max-w-full rounded-lg border border-[#3a3b42] bg-[#1d1e22]">
        <div className="flex items-start gap-3 border-b border-[#2f3037] px-6 pt-5 pb-4">
          <div className="flex-1">
            <h2 id="train-title" className="text-[17px] font-semibold">
              {run ? (ended ? (ended.status === 'succeeded' ? 'LoRA ready' : ended.status === 'cancelled' ? 'Training cancelled' : 'Training failed') : 'Training LoRA') : 'Train LoRA'}
            </h2>
            <p className="mt-1.5 text-[12.5px] text-muted">
              {(pre?.base_model?.name || 'Active model') + ' · approved conversations only'}
              {pre?.simulate || run?.config?.simulate ? ' · simulated' : ''}
            </p>
          </div>
          <button aria-label="Close" onClick={onClose} className="grid size-8 place-items-center rounded text-muted hover:text-gray-100">
            <LuX className="size-5" />
          </button>
        </div>

        {!run && pre && (
          <div className="flex flex-col gap-5 px-6 py-5 text-sm">
            <div className="flex gap-10">
              <div>
                <div className="text-xs text-muted">Examples</div>
                <div className="mt-1.5 font-mono text-[26px]">{pre.export.examples}</div>
              </div>
              <div>
                <div className="text-xs text-muted">From conversations</div>
                <div className="mt-1.5 font-mono text-[26px]">{pre.export.conversations}</div>
              </div>
            </div>
            {pre.export.after_cap.length > 0 && (
              <div>
                <div className="mb-2 text-xs text-muted">Examples per person after the author cap</div>
                {pre.export.after_cap.map((a) => {
                  const before = pre.export.by_author.find((b) => b.name === a.name)
                  return (
                    <div key={a.name} className="flex items-center gap-3 py-0.5 text-[13px]">
                      <span className="w-44 truncate">{a.name}</span>
                      <span className="h-1 flex-1 bg-[#2f3037]">
                        <span className="block h-full bg-gray-200" style={{ width: pct(a.share) }} />
                      </span>
                      <span className="w-24 text-right font-mono text-xs text-muted">
                        {pct(a.share)}
                        {before && before.count !== a.count ? ` (was ${pct(before.share)})` : ''}
                      </span>
                    </div>
                  )
                })}
              </div>
            )}
            <p className="text-xs text-muted">
              Left out: {Object.entries(pre.export.dropped).filter(([, n]) => n).map(([k, n]) => `${n} ${k}`).join(', ') || 'nothing'}.
            </p>
            {!pre.trainable && !pre.simulate && (
              <p className="text-[13px] text-[#E0B25C]">
                This model has no Hugging Face weights to train on. Set them under “Trains on” in the model menu.
              </p>
            )}
            {pre.gpu ? (
              <div className="border-t border-[#2f3037] pt-4">
                <div className="flex justify-between text-[13px]">
                  <span>{pre.gpu.name}</span>
                  <span className={cx('font-mono text-xs', pre.enough_vram ? 'text-muted' : 'text-[#E0B25C]')}>
                    {pre.gpu.free_gb} GB free · needs about {pre.need_gb} GB
                  </span>
                </div>
                {!pre.enough_vram && pre.gpu.processes.length > 0 && (
                  <div className="mt-3">
                    <p className="text-xs leading-relaxed text-muted">
                      Pause these while training. llama-server comes back on its own with the new adapter. Start anything else again yourself.
                    </p>
                    {pre.gpu.processes.map((p) => (
                      <label key={p.pid} className="mt-2 flex items-center gap-2.5 text-[13px]">
                        <input
                          type="checkbox"
                          checked={stop.includes(p.pid)}
                          onChange={(e) => setStop((s) => (e.target.checked ? [...s, p.pid] : s.filter((x) => x !== p.pid)))}
                          className="accent-gray-200"
                        />
                        <span className="flex-1">{p.name}</span>
                        <span className="font-mono text-xs text-muted">{p.used_gb} GB</span>
                      </label>
                    ))}
                  </div>
                )}
              </div>
            ) : (
              !pre.simulate && <p className="text-[13px] text-[#E0B25C]">No NVIDIA GPU found on this machine.</p>
            )}
            <div className="font-mono text-[11.5px] text-muted">rank 16 · alpha 32 · lr 2e-4 · 3 epochs · max len 2048 · QLoRA</div>
          </div>
        )}

        {run && (
          <div className="flex flex-col gap-5 px-6 py-5">
            <div className="flex gap-10">
              <div>
                <div className="text-xs text-muted">Elapsed</div>
                <div className="mt-1.5 font-mono text-[28px]">{fmtDuration(elapsed)}</div>
              </div>
              <div>
                <div className="text-xs text-muted">Remaining</div>
                <div className="mt-1.5 font-mono text-[28px] text-gray-400">{ended ? '—' : progress?.eta_s ? `~${fmtDuration(progress.eta_s)}` : '…'}</div>
              </div>
              <div className="flex-1" />
              <div className="text-right">
                <div className="text-xs text-muted">Overall</div>
                <div className="mt-1.5 font-mono text-[28px]">{pct(overall)}</div>
              </div>
            </div>
            <div role="progressbar" aria-valuenow={Math.round(overall * 100)} aria-valuemin={0} aria-valuemax={100} className="h-[3px] bg-[#2f3037]">
              <div className="h-full bg-gray-100 transition-[width]" style={{ width: pct(overall) }} />
            </div>
            <div>
              {STAGES.map(([key, label], i) => (
                <div key={key}>
                  <div className={cx('grid min-h-10 grid-cols-[20px_minmax(0,1fr)_auto] items-center gap-3 text-[13.5px]', i > stageIndex && !ended && 'text-muted')}>
                    {statusIcon(i, key)}
                    <span className={cx(i === stageIndex && !ended && 'font-semibold')}>
                      {key === 'train' && progress?.stage === 'train' && progress.total_steps
                        ? `Train · epoch ${progress.epoch} of ${progress.epochs} · step ${progress.step} / ${progress.total_steps}`
                        : label}
                    </span>
                    <span className="font-mono text-xs text-muted">
                      {stageTimes[STAGES[i + 1]?.[0]] != null && stageTimes[key] != null
                        ? fmtDuration(stageTimes[STAGES[i + 1][0]] - stageTimes[key])
                        : ''}
                    </span>
                  </div>
                  {key === 'train' && spark && (
                    <div className="mb-2 ml-8 flex items-end gap-3">
                      <svg role="img" aria-label="Training loss" width={spark.w} height={spark.h} className="max-w-full">
                        <path d={spark.d} fill="none" stroke="#ECECEE" strokeWidth={1.5} />
                      </svg>
                      <span className="text-xs leading-relaxed text-muted">
                        loss <span className="font-mono text-gray-100">{losses[losses.length - 1].loss.toFixed(2)}</span>
                        <br />
                        from <span className="font-mono">{losses[0].loss.toFixed(2)}</span>
                      </span>
                    </div>
                  )}
                </div>
              ))}
            </div>
            {ended?.status === 'succeeded' && (
              <p className="text-[13px] text-[#6CCB98]">The adapter is in the model list and set as active.</p>
            )}
            {ended?.status === 'failed' && <pre className="max-h-40 overflow-auto font-mono text-xs whitespace-pre-wrap text-[#F28B82]">{ended.error}</pre>}
            {run.stopped_services.some((s) => !s.is_llama) && ended && (
              <p className="text-xs text-[#E0B25C]">
                Start these again yourself: {run.stopped_services.filter((s) => !s.is_llama).map((s) => s.name).join(', ')}.
              </p>
            )}
            <div className="border-t border-[#2f3037] pt-3.5 font-mono text-[11.5px] text-muted">
              {run.example_count} examples · rank {run.config.r} · alpha {run.config.alpha} · lr {run.config.lr} · {run.config.epochs} epochs · max len {run.config.max_len}
            </div>
          </div>
        )}

        {!run && !pre && <div className="px-6 py-10 text-sm text-muted">Checking…</div>}

        <div className="flex justify-end gap-2 border-t border-[#2f3037] px-6 py-3.5">
          {run && !ended && (
            <>
              <button onClick={cancel} className="h-9 rounded-md border border-line px-3.5 text-[13px] text-gray-300 hover:bg-raised">
                Cancel run
              </button>
              <button onClick={onClose} className="h-9 rounded-md bg-gray-100 px-3.5 text-[13px] font-semibold text-ink hover:bg-white">
                Run in background
              </button>
            </>
          )}
          {!run && pre && (
            <button
              onClick={start}
              disabled={starting || pre.export.examples === 0 || (!pre.trainable && !pre.simulate) || (!pre.simulate && !pre.gpu)}
              className="h-9 rounded-md bg-gray-100 px-3.5 text-[13px] font-semibold text-ink hover:bg-white disabled:opacity-40"
            >
              {starting ? 'Starting…' : pre.export.examples === 0 ? 'Approve a conversation first' : 'Start training'}
            </button>
          )}
          {ended && (
            <button onClick={onClose} className="h-9 rounded-md bg-gray-100 px-3.5 text-[13px] font-semibold text-ink hover:bg-white">
              Done
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
