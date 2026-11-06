import { useEffect, useState } from 'react'
import { LuHeadphones, LuDatabase, LuMic, LuVolume2, LuEye, LuPuzzle } from 'react-icons/lu'
import PageHeader from './PageHeader'

// Keyed by the module folder name the Python side registers with.
const ICON = { discord: LuHeadphones, memory: LuDatabase, stt: LuMic, tts: LuVolume2, vision: LuEye }

const Modules = () => {
  const [modules, setModules] = useState({})
  useEffect(() => {
    window.api.getModules().then(setModules)
  }, [])
  const names = Object.keys(modules)

  return (
    <>
      <PageHeader title="Modules" sub={`${names.length} registered by the Python side`} />
      {names.length === 0 && <p className="text-muted">No modules have registered yet.</p>}
      <div className="grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-4">
        {names.map((name) => {
          const Icon = ICON[name] ?? LuPuzzle
          return (
            <article key={name} className="panel flex min-h-48 flex-col gap-4 p-5">
              <Icon className="size-7 text-white" />
              <div className="flex items-center gap-2">
                <h2 className="font-display text-xl capitalize">{name}</h2>
                {modules[name].required && (
                  <span className="rounded border border-amber-900 px-1.5 py-0.5 text-[10px] tracking-widest text-accent">
                    REQUIRED
                  </span>
                )}
              </div>
              <p className="text-sm text-muted">{modules[name].description}</p>
            </article>
          )
        })}
      </div>
    </>
  )
}

export default Modules
