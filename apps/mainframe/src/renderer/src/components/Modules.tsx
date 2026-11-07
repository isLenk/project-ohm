import { useState } from 'react'
import { Link } from 'react-router-dom'
import {
  LuEar,
  LuEye,
  LuHeadphones,
  LuPuzzle,
  LuRotateCcw,
  LuDatabase,
  LuVolume2
} from 'react-icons/lu'
import PageHeader from './PageHeader'
import { Loading, Spinner } from './Loader'
import { message, moduleName, useEngine, usePoll, type Module } from '../lib/ohm'

// Keyed by the module folder name the Python side registers with.
const ICON = {
  discord: LuHeadphones,
  memory: LuDatabase,
  stt: LuEar,
  tts: LuVolume2,
  vision: LuEye
}

const Modules = () => {
  const engine = useEngine()
  const [tick, setTick] = useState(0)
  const { data, error: loadError } = usePoll<Module[]>(() => window.api.listModules(), 3000, [tick])
  const [changed, setChanged] = useState(false) // toggled while the engine was running
  const [error, setError] = useState('')
  const [restarting, setRestarting] = useState(false)
  const modules = data ?? []
  const running = engine?.state === 'running'

  const act = (fn: () => Promise<any>) =>
    fn()
      .then(() => (setError(''), setTick((t) => t + 1)))
      .catch((e) => setError(message(e)))

  const toggle = (m: Module) =>
    act(async () => {
      await window.api.setModuleEnabled(m.name, !m.enabled)
      if (running) setChanged(true)
    })

  const status = (m: Module) => {
    if (!m.enabled) return ['Turned off', 'bg-[#4a4d55]']
    if (m.restarting) return ['Restarting…', 'bg-warn']
    if (!running) return ['Engine stopped', 'bg-[#4a4d55]']
    if (m.ok === null) return ['Waiting…', 'bg-warn']
    return m.ok ? ['Running', 'bg-good'] : ['Failing', 'bg-bad']
  }

  return (
    <>
      <PageHeader
        title="Modules"
        sub={`${modules.filter((m) => m.enabled).length} of ${modules.length} on`}
      >
        <Link to="/" className="text-[12.5px] text-soft hover:text-gray-100">
          Uptime on the Dashboard
        </Link>
      </PageHeader>

      {changed && (
        <div className="flex flex-wrap items-center gap-3 border-b border-line bg-[#1e1b14] px-8 py-2.5 text-[12.5px] text-[#f2d49a]">
          <span className="flex-1">
            Turning modules on or off takes effect when the engine restarts.
          </span>
          <button
            className="btn"
            disabled={restarting}
            onClick={() => {
              setRestarting(true)
              act(async () => (await window.api.restartEngine(), setChanged(false))).finally(() =>
                setRestarting(false)
              )
            }}
          >
            {restarting ? <Spinner /> : <LuRotateCcw />}
            {restarting ? 'Restarting…' : 'Restart engine'}
          </button>
        </div>
      )}
      {error && <p className="border-b border-line px-8 py-2 text-[12.5px] text-bad">{error}</p>}

      <div className="px-8 pt-2 pb-12">
        <div className="max-w-[900px]">
          {!data && !loadError && <Loading label="Loading modules…" />}
          {!data && loadError && <p className="py-10 text-sm text-bad">{loadError}</p>}
          {modules.map((m) => {
            const Icon = ICON[m.name] ?? LuPuzzle
            const [text, dot] = status(m)
            return (
              <div
                key={m.name}
                className={`grid grid-cols-[44px_minmax(0,1fr)_auto_auto_44px] items-center gap-x-5 gap-y-1.5 border-b border-line py-4.5 ${m.enabled ? '' : 'text-muted'}`}
              >
                <span className="row-span-2 grid size-11 place-items-center rounded-lg border border-edge text-[#c9cbd1]">
                  <Icon className="size-5" />
                </span>
                <span className="flex items-center gap-2.5">
                  <span className="text-[15px] font-semibold">{moduleName(m.name)}</span>
                  {m.required && (
                    <span className="rounded border border-[#5a3a2e] px-1.5 py-1 text-[10.5px] leading-none font-semibold tracking-wider text-clay">
                      REQUIRED
                    </span>
                  )}
                </span>
                <span
                  className="row-span-2 flex min-w-30 items-center gap-2 text-[12.5px] text-soft"
                  title={m.detail}
                >
                  <span className={`size-2 rounded-full ${dot}`} />
                  {text}
                </span>
                <button
                  className="btn row-span-2 size-9 px-0"
                  aria-label={`Restart ${moduleName(m.name)}`}
                  title={running ? `Restart ${moduleName(m.name)}` : 'Start the engine first'}
                  disabled={!running || !m.enabled || m.restarting}
                  onClick={() => act(() => window.api.restartModule(m.name))}
                >
                  <LuRotateCcw className="size-4" />
                </button>
                {/* Required modules always run, so they get no switch */}
                {m.required ? (
                  <span className="row-span-2" />
                ) : (
                  <button
                    role="switch"
                    aria-checked={m.enabled}
                    aria-label={`${moduleName(m.name)} on`}
                    onClick={() => toggle(m)}
                    className={`relative row-span-2 h-6 w-11 cursor-pointer rounded-full ${m.enabled ? 'bg-accent' : 'bg-[#3a3c42]'}`}
                  >
                    <span
                      className={`absolute top-1 size-4 rounded-full bg-white transition-[left] ${m.enabled ? 'left-6' : 'left-1'}`}
                    />
                  </button>
                )}
                <span className="text-[13px] text-muted">
                  {running && m.enabled && m.detail ? m.detail : m.description}
                </span>
              </div>
            )
          })}
          <p className="mt-5 text-[12.5px] leading-relaxed text-muted">
            Turning a module off keeps it off after restarts. Required modules always run. Restart
            resets a module inside the running engine: Memory reconnects to the NAS, Text to speech
            reconnects to its server, and Discord and Speech to text rejoin the voice call.
          </p>
        </div>
      </div>
    </>
  )
}

export default Modules
