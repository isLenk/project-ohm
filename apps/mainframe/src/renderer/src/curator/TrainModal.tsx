import { useEffect, useMemo, useState } from 'react'
import { LuCheck, LuLoader, LuX } from 'react-icons/lu'
import { followJob, get, post, stream } from './api'
import { REPLIES_AIM, REPLIES_LOW, cx, fmtDuration, pct } from './util'
import { Loading } from '../components/Loader'

/** From a cloud run's "cloud" events (train/cloud.py) */
type Cloud = {
  phase?: string
  gpu?: string
  price_hr?: number | null
  url?: string
  app_id?: string
  state?: string | null
  gpuUpAt?: number
  stoppedAt?: number
}

const CLOUD_PHASES: Record<string, string> = {
  building: 'Building the training environment on Modal (the first run takes a few minutes)',
  waiting_gpu: 'Waiting for a cloud GPU',
  gpu_up: 'Training on the cloud GPU',
  sending_adapter: 'Sending the adapter back',
  stopping: 'Stopping the cloud GPU',
  stopped: 'Cloud GPU stopped. Modal confirms it, so nothing is billing.',
  check: 'Couldn’t confirm the cloud GPU stopped. Open Modal and check the app is stopped.'
}

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
    /** Replies the judge model has and hasn't read (train/checks.py). */
    checked: { checked: number; unchecked: number }
  }
  gpu: { name: string; total_gb: number; free_gb: number; processes: { pid: number; name: string; used_gb: number }[] } | null
  need_gb: number
  enough_vram: boolean
  active_run: { id: string } | null
  /** Modal training (train/cloud.py) */
  cloud: { ready: boolean; why: string | null; gpu: string }
  /** Cloud runs need a name list to mask with */
  names_known: boolean
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

type Props = { onClose: () => void; onError: (e: unknown) => void; onFinished: () => void; onEvaluate: (adapterId: string) => void }

export default function TrainModal({ onClose, onError, onFinished, onEvaluate }: Props) {
  const [pre, setPre] = useState<Preflight | null>(null)
  const [run, setRun] = useState<Run | null>(null)
  const [progress, setProgress] = useState<any>(null)
  const [stageTimes, setStageTimes] = useState<Record<string, number>>({})
  const [losses, setLosses] = useState<{ step: number; loss: number }[]>([])
  // Loss on held-out examples, when the worker measures it (older runs and simulated ones may not).
  const [evals, setEvals] = useState<{ step: number; loss: number }[]>([])
  const [evalLoss, setEvalLoss] = useState<{ last: number | null; best: number | null }>({ last: null, best: null })
  const [stop, setStop] = useState<number[]>([])
  const [ended, setEnded] = useState<{ status: string; error?: string; adapter_id?: string | null } | null>(null)
  const [now, setNow] = useState(Date.now())
  const [starting, setStarting] = useState(false)
  const [checking, setChecking] = useState<string | null>(null)
  const [target, setTarget] = useState<'local' | 'cloud'>('local')
  const [note, setNote] = useState<string | null>(null)
  const [cloud, setCloud] = useState<Cloud>({})

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
        // The stream replays every event so far, so skip steps we already have from run.losses
        if (d.loss != null) setLosses((l) => (l.some((x) => x.step === d.step) ? l : [...l, { step: d.step, loss: d.loss }].sort((a, b) => a.step - b.step)))
        if (d.eval_loss != null && d.step != null)
          setEvals((l) => (l.some((x) => x.step === d.step) ? l : [...l, { step: d.step, loss: d.eval_loss }].sort((a, b) => a.step - b.step)))
        if (d.eval_loss != null || d.best_eval_loss != null)
          setEvalLoss((e) => ({ last: d.eval_loss ?? e.last, best: d.best_eval_loss ?? e.best }))
      } else if (name === 'done') {
        if (d?.eval_loss != null || d?.best_eval_loss != null)
          setEvalLoss((e) => ({ last: d.eval_loss ?? e.last, best: d.best_eval_loss ?? e.best }))
      } else if (name === 'note') setNote(d.message)
      else if (name === 'cloud')
        setCloud((c) => ({
          ...c,
          ...d,
          gpuUpAt: d.phase === 'gpu_up' ? d.at * 1000 : c.gpuUpAt,
          stoppedAt: d.phase === 'stopping' ? d.at * 1000 : c.stoppedAt
        }))
      else if (name === 'stage') setStageTimes((s) => ({ ...s, [d.stage]: d.elapsed_s }))
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

  // The judge reads every reply that would be trained and the export leaves out the ones that don't answer.
  const checkReplies = async () => {
    setChecking('Starting the judge…')
    try {
      const j = await post<{ id: string }>('/train/check-replies')
      await followJob(j.id, (p) => setChecking(p.note || 'Checking…'))
      setPre(await get<Preflight>('/train/preflight'))
    } catch (e) {
      onError(e)
    } finally {
      setChecking(null)
    }
  }

  const start = async () => {
    setStarting(true)
    try {
      setRun(await post<Run>('/train', { stop_pids: target === 'cloud' ? [] : stop, config: { target } }))
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
    // One scale for both lines, so a gap between them reads as overfitting
    const all = [...losses, ...evals].map((l) => l.loss)
    const max = Math.max(...all)
    const min = Math.min(...all)
    const total = progress?.total_steps || losses[losses.length - 1].step
    const xy = (l: { step: number; loss: number }) => `${(l.step / total) * w},${4 + (1 - (l.loss - min) / (max - min || 1)) * (h - 8)}`
    return { d: `M${losses.map(xy).join(' L')}`, evalD: evals.length > 1 ? `M${evals.map(xy).join(' L')}` : null, w, h }
  }, [losses, evals, progress?.total_steps])

  // What the adapter learns from: Ohm's turns across every example, after the author cap.
  const replies = pre ? pre.export.after_cap.reduce((n, a) => n + a.count, 0) : 0

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
              <div>
                <div className="text-xs text-muted">Replies to learn from</div>
                <div className={cx('mt-1.5 font-mono text-[26px]', replies < REPLIES_LOW && 'text-[#E0B25C]')}>{replies.toLocaleString()}</div>
                <div className="mt-0.5 text-xs text-muted">aim for {REPLIES_AIM.toLocaleString()}+</div>
              </div>
            </div>
            {replies > 0 && replies < REPLIES_LOW && (
              <p className="text-[13px] leading-relaxed text-[#E0B25C]">
                Under {REPLIES_LOW.toLocaleString()} replies the LoRA mostly memorizes these few conversations instead of learning how the group talks. Use “Find good
                conversations” under the conversation list to approve more first.
              </p>
            )}
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
            {pre.export.checked && (
              <div className="flex items-center gap-3 text-xs leading-relaxed text-muted">
                <span className="flex-1">
                  {checking ? (
                    <span className="inline-flex items-center gap-2">
                      <LuLoader className="size-3.5 animate-spin" />
                      {checking}. Ohm’s llama-server is stopped until it’s done.
                    </span>
                  ) : pre.export.checked.unchecked ? (
                    `The judge model hasn’t checked ${pre.export.checked.unchecked.toLocaleString()} of these replies yet. Replies it says don’t answer the message before them are left out, so Ohm doesn’t learn to ignore people.`
                  ) : (
                    'The judge model has checked every reply.'
                  )}
                </span>
                {!checking && pre.export.checked.unchecked > 0 && (
                  <button onClick={checkReplies} className="h-8 shrink-0 rounded-md border border-line px-3 text-[12.5px] text-gray-200 hover:bg-menu-hover">
                    Check them
                  </button>
                )}
              </div>
            )}
            {!pre.trainable && !pre.simulate && (
              <p className="text-[13px] text-[#E0B25C]">
                This model has no Hugging Face weights to train on. Set them under “Trains on” in the model menu.
              </p>
            )}
            <div className="flex items-center gap-3 border-t border-[#2f3037] pt-4 text-[13px]">
              <span className="text-muted">Train on</span>
              {(['local', 'cloud'] as const).map((t) => (
                <button
                  key={t}
                  onClick={() => setTarget(t)}
                  className={cx('h-8 rounded-md border px-3 text-[12.5px]', target === t ? 'border-gray-300 bg-[#26272d] text-gray-100' : 'border-line text-gray-400 hover:bg-menu-hover')}
                >
                  {t === 'local' ? 'This PC' : `Cloud · ${pre.cloud?.gpu ?? 'Modal'}`}
                </button>
              ))}
            </div>
            {target === 'cloud' ? (
              <div className="text-[13px] leading-relaxed">
                {pre.cloud?.ready ? (
                  <p className="text-muted">
                    Trains on a rented {pre.cloud.gpu} on Modal, usually well under a dollar a run, billed by the second and stopped the moment it’s done. Ohm stays online.
                    Only the messages go up, with everyone’s names and contact details swapped for stand-ins.
                  </p>
                ) : (
                  <p className="text-[#E0B25C]">{pre.cloud?.why ?? 'Cloud training isn’t set up.'}</p>
                )}
                {!pre.names_known && (
                  <p className="mt-2 text-[#E0B25C]">No name list is loaded, so names couldn’t be masked. Open the project or check the NAS.</p>
                )}
              </div>
            ) : pre.gpu ? (
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
            <div className="font-mono text-[11.5px] text-muted">rank 16 · alpha 32 · lr 2e-4 · 1 epoch · max len 2048 · QLoRA</div>
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
                      <svg role="img" aria-label={spark.evalD ? 'Training loss, and eval loss dashed' : 'Training loss'} width={spark.w} height={spark.h} className="max-w-full">
                        <path d={spark.d} fill="none" stroke="#ECECEE" strokeWidth={1.5} />
                        {spark.evalD && <path d={spark.evalD} fill="none" stroke="#5EC8E5" strokeWidth={1.5} strokeDasharray="4 3" />}
                      </svg>
                      <span className="text-xs leading-relaxed text-muted">
                        loss <span className="font-mono text-gray-100">{losses[losses.length - 1].loss.toFixed(2)}</span>
                        <br />
                        from <span className="font-mono">{losses[0].loss.toFixed(2)}</span>
                        {evalLoss.last != null && (
                          <>
                            <br />
                            <span title="Loss on held-out examples it doesn't train on. If it rises while loss falls, it's memorizing.">
                              eval <span className="font-mono text-[#5EC8E5]">{evalLoss.last.toFixed(2)}</span>
                              {evalLoss.best != null && <> · best <span className="font-mono">{evalLoss.best.toFixed(2)}</span></>}
                            </span>
                          </>
                        )}
                      </span>
                    </div>
                  )}
                </div>
              ))}
            </div>
            {run.config?.target === 'cloud' && (
              <div className="rounded-md border border-[#2f3037] px-3.5 py-3 text-[13px]">
                <div className="flex items-center gap-2.5">
                  {cloud.phase === 'stopped' ? (
                    <LuCheck className="size-4 text-[#6CCB98]" />
                  ) : cloud.phase === 'check' ? (
                    <LuX className="size-4 text-[#E0B25C]" />
                  ) : (
                    <LuLoader className="size-4 animate-spin text-muted" />
                  )}
                  <span className={cx('flex-1', cloud.phase === 'check' && 'text-[#E0B25C]')}>
                    {CLOUD_PHASES[cloud.phase ?? ''] ?? 'Starting the cloud run'}
                  </span>
                  {cloud.url && (
                    <a href={cloud.url} target="_blank" rel="noreferrer" className="text-xs text-gray-300 underline underline-offset-2 hover:text-white">
                      Open in Modal
                    </a>
                  )}
                </div>
                <div className="mt-1.5 pl-6.5 font-mono text-[11.5px] text-muted">
                  {cloud.gpu ?? run.config?.cloud_gpu ?? 'GPU'}
                  {cloud.gpuUpAt != null && (
                    <>
                      {' · GPU time '}
                      {fmtDuration(Math.max(0, ((cloud.stoppedAt ?? now) - cloud.gpuUpAt) / 1000))}
                      {cloud.price_hr != null &&
                        ` · about ${((((cloud.stoppedAt ?? now) - cloud.gpuUpAt) / 3600000) * cloud.price_hr).toFixed(2)} so far`}
                    </>
                  )}
                  {cloud.state && ` · Modal says: ${cloud.state}`}
                </div>
              </div>
            )}
            {note && <p className="text-xs leading-relaxed text-muted">{note}</p>}
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
              {run.config.target === 'cloud' ? 'cloud · ' : ''}
              {run.example_count} examples · rank {run.config.r} · alpha {run.config.alpha} · lr {run.config.lr} · {run.config.epochs} epochs · max len {run.config.max_len}
            </div>
          </div>
        )}

        {!run && !pre && <Loading label="Checking the GPU and training data…" className="px-6" />}

        <div className="flex justify-end gap-2 border-t border-[#2f3037] px-6 py-3.5">
          {run && !ended && (
            <>
              <button onClick={cancel} className="h-9 rounded-md border border-line px-3.5 text-[13px] text-gray-300 hover:bg-menu-hover">
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
              disabled={starting || !!checking || pre.export.examples === 0 || (!pre.trainable && !pre.simulate) ||
                (target === 'cloud' ? (!pre.cloud?.ready && !pre.simulate) || !pre.names_known : !pre.simulate && !pre.gpu)
              }
              className="h-9 rounded-md bg-gray-100 px-3.5 text-[13px] font-semibold text-ink hover:bg-white disabled:opacity-40"
            >
              {starting ? 'Starting…' : pre.export.examples === 0 ? 'Approve a conversation first' : 'Start training'}
            </button>
          )}
          {ended?.status === 'succeeded' && ended.adapter_id && (
            <button
              onClick={() => onEvaluate(ended.adapter_id!)}
              className="h-9 rounded-md border border-line px-3.5 text-[13px] text-gray-300 hover:bg-menu-hover"
            >
              Score it
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
