// Scores a trained LoRA with a bigger local judge model (curator_service/app/train/evaluate.py).
import { useCallback, useEffect, useState } from 'react'
import { LuCheck, LuX } from 'react-icons/lu'
import { followJob, get, post } from './api'
import { cx, pct } from './util'
import { Loading, Spinner } from '../components/Loader'

type Variant = 'real' | 'base' | 'adapter' | 'partial'
type Stats = { answers: number; n: number; passes_as_real?: number }
type Group = { items: number; skipped: number; variants: Partial<Record<Variant, Stats>> }
type Item = {
  kind: 'held_out' | 'probe'
  speaker: string | null
  chat: string
  real: string | null
  replies: Record<'base' | 'adapter' | 'partial', string>
  answers: Partial<Record<Variant, boolean>> | null
  passes: Partial<Record<Variant, boolean | null>> | null
}
type Result =
  | { exists: false; trained_here: boolean; judge: string; judge_ready: boolean }
  | {
      exists: true
      adapter_name: string
      created_at: number
      judge: string
      partial_scale: number
      summary: { held_out: Group; probe: Group }
      items: Item[]
    }

const ORDER: Variant[] = ['real', 'adapter', 'partial', 'base']

type Props = { adapterId: string; onClose: () => void; onError: (e: unknown) => void }

export default function EvalModal({ adapterId, onClose, onError }: Props) {
  const [res, setRes] = useState<Result | null>(null)
  const [running, setRunning] = useState<string | null>(null)
  const [only, setOnly] = useState<'missed' | 'all'>('missed')

  const load = useCallback(async () => {
    try {
      setRes(await get<Result>(`/eval/${adapterId}`))
    } catch (e) {
      onError(e)
    }
  }, [adapterId, onError])

  const follow = useCallback(
    async (jobId: string) => {
      setRunning('Starting…')
      try {
        await followJob(jobId, (p) => setRunning(p.note || 'Working…'))
        await load()
      } catch (e) {
        onError(e)
      } finally {
        setRunning(null)
      }
    },
    [load, onError]
  )

  useEffect(() => {
    load()
    // Pick up an evaluation that's already going (the window was closed and opened again)
    get<{ jobs: any[] }>('/jobs')
      .then((r) => {
        const j = r.jobs.find((x) => x.kind === 'eval' && x.status === 'running')
        if (j) follow(j.id)
      })
      .catch(() => {})
  }, [load, follow])

  const start = async () => {
    try {
      const j = await post<{ id: string }>(`/eval/${adapterId}`)
      follow(j.id)
    } catch (e) {
      onError(e)
    }
  }

  const label = (v: Variant, scale = 0.6) =>
    v === 'real' ? 'Their real reply' : v === 'base' ? 'Base model, no LoRA' : v === 'adapter' ? 'LoRA' : `LoRA at ${Math.round(scale * 100)}%`

  const table = (g: Group, scale: number) => (
    <table className="w-full text-[13px]">
      <thead>
        <tr className="text-left text-xs text-muted">
          <th className="py-1 font-normal" />
          <th className="py-1 text-right font-normal" title="Share of replies the judge says respond to the last message">
            Answers it
          </th>
          <th
            className="py-1 text-right font-normal"
            title="Shown next to the person's real reply, how often the judge picked this one as the one a person wrote. 50% means it can't tell them apart."
          >
            Passes as real
          </th>
        </tr>
      </thead>
      <tbody>
        {ORDER.filter((v) => g.variants[v]).map((v) => {
          const s = g.variants[v]!
          return (
            <tr key={v} className="border-t border-[#2f3037]">
              <td className={cx('py-1.5', v === 'real' && 'text-muted')}>{label(v, scale)}</td>
              <td className="py-1.5 text-right font-mono">{pct(s.answers)}</td>
              <td className="py-1.5 text-right font-mono">{s.passes_as_real != null ? pct(s.passes_as_real) : ''}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )

  const done = res?.exists ? res : null
  const shown = done
    ? done.items.filter((it) => only === 'all' || it.answers?.adapter === false)
    : []

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/55 p-4">
      <div role="dialog" aria-labelledby="eval-title" className="flex max-h-[90vh] w-[720px] max-w-full flex-col rounded-lg border border-[#3a3b42] bg-[#1d1e22]">
        <div className="flex items-start gap-3 border-b border-[#2f3037] px-6 pt-5 pb-4">
          <div className="flex-1">
            <h2 id="eval-title" className="text-[17px] font-semibold">
              Score {done?.adapter_name || 'this LoRA'}
            </h2>
            <p className="mt-1.5 text-[12.5px] text-muted">
              {done
                ? `Judged by ${done.judge} · ${new Date(done.created_at).toLocaleString()}`
                : 'Replies to chats it never trained on, judged by a bigger model'}
            </p>
          </div>
          <button aria-label="Close" onClick={onClose} className="grid size-8 place-items-center rounded text-muted hover:text-gray-100">
            <LuX className="size-5" />
          </button>
        </div>

        <div className="flex min-h-0 flex-col gap-5 overflow-y-auto px-6 py-5">
          {!res && <Loading label="Looking for an earlier score…" />}
          {running && (
            <div role="status" className="flex items-center gap-2.5 text-[13px]">
              <Spinner className="size-4" />
              {running}
            </div>
          )}
          {res && !res.exists && !running && (
            <div className="text-[13px] leading-relaxed text-muted">
              {!res.trained_here ? (
                <p className="text-[#E0B25C]">Only adapters trained here can be scored: it uses the conversations the run held out.</p>
              ) : !res.judge_ready ? (
                <p className="text-[#E0B25C]">The judge model ({res.judge}) isn’t in llama.cpp’s models folder.</p>
              ) : (
                <p>
                  Ohm answers each held-out chat three ways (no LoRA, the LoRA, and the LoRA at 60%), plus a few test messages. Then {res.judge} judges whether
                  each reply answers the last message, and whether it can tell each one from the person’s real reply. Ohm’s llama-server stops for a few minutes while the judge
                  runs, and comes back on its own.
                </p>
              )}
            </div>
          )}
          {done && (
            <>
              <div>
                <div className="mb-1 text-xs text-muted">Held-out chats ({done.summary.held_out.items})</div>
                {table(done.summary.held_out, done.partial_scale)}
              </div>
              <div>
                <div className="mb-1 text-xs text-muted">Test messages, like “can you hear me?” ({done.summary.probe.items})</div>
                {table(done.summary.probe, done.partial_scale)}
              </div>
              <div>
                <div className="mb-2 flex items-center gap-3 text-xs text-muted">
                  <span className="flex-1">Replies</span>
                  {(['missed', 'all'] as const).map((k) => (
                    <button key={k} onClick={() => setOnly(k)} className={cx('rounded px-2 py-0.5', only === k ? 'bg-[#2f3037] text-gray-100' : 'hover:text-gray-200')}>
                      {k === 'missed' ? 'Where the LoRA missed' : 'All'}
                    </button>
                  ))}
                </div>
                {shown.length === 0 && <p className="text-[13px] text-muted">None.</p>}
                {shown.map((it, i) => (
                  <div key={i} className="border-t border-[#2f3037] py-3 text-[13px]">
                    <pre className="mb-2 max-h-28 overflow-hidden font-sans text-xs whitespace-pre-wrap text-muted">{it.chat.split('\n').slice(-5).join('\n')}</pre>
                    {ORDER.filter((v) => (v === 'real' ? it.real : it.replies[v])).map((v) => {
                      const ok = it.answers?.[v]
                      return (
                        <div key={v} className="flex gap-2 py-0.5">
                          <span className="w-4 shrink-0 pt-0.5">
                            {ok === true ? <LuCheck className="size-3.5 text-[#6CCB98]" /> : ok === false ? <LuX className="size-3.5 text-[#F28B82]" /> : null}
                          </span>
                          <span className="w-32 shrink-0 text-xs text-muted">{label(v, done.partial_scale)}</span>
                          <span className="flex-1 whitespace-pre-wrap">{v === 'real' ? it.real : it.replies[v] || '(no reply)'}</span>
                          {it.passes?.[v] === true && (
                            <span className="shrink-0 self-start rounded bg-[#2f3037] px-1.5 py-0.5 text-[11px] text-gray-300" title="The judge took it for the real reply">
                              passed as real
                            </span>
                          )}
                        </div>
                      )
                    })}
                  </div>
                ))}
              </div>
            </>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t border-[#2f3037] px-6 py-3.5">
          <button onClick={onClose} className="h-9 rounded-md border border-line px-3.5 text-[13px] text-gray-300 hover:bg-menu-hover">
            Close
          </button>
          {res && (res.exists || (res.trained_here && res.judge_ready)) && (
            <button onClick={start} disabled={!!running} className="h-9 rounded-md bg-gray-100 px-3.5 text-[13px] font-semibold text-ink hover:bg-white disabled:opacity-40">
              {running ? 'Scoring…' : res.exists ? 'Score again' : 'Score it'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
