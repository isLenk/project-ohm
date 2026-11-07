import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import {
  LuLayoutGrid,
  LuUsers,
  LuMessagesSquare,
  LuLayers,
  LuDatabase,
  LuFilter,
  LuSlidersHorizontal,
  LuPower
} from 'react-icons/lu'
import logo from '../assets/ohm-logo.svg'
import EnginePanel from './EnginePanel'
import { useEngine, usePoll } from '../lib/ohm'
import { get } from '../curator/api'

const LINKS = [
  ['/', 'Dashboard', LuLayoutGrid],
  ['/characters', 'Characters', LuUsers],
  ['/curator', 'Curator', LuMessagesSquare],
  ['/modules', 'Modules', LuLayers],
  ['/memories', 'Memories', LuDatabase],
  ['/filters', 'Filters', LuFilter],
  ['/settings', 'Settings', LuSlidersHorizontal]
] as const

const tile =
  'grid size-12 place-items-center rounded-[10px] text-soft hover:bg-raised hover:text-gray-100'

const Sidebar = () => {
  const [open, setOpen] = useState(false)
  const engine = useEngine()
  const { data: llm } = usePoll(() => get('/llm/status'), 10000)

  // Green when everything is up, grey when it's all off, amber for anything in between
  const py = engine?.state === 'running'
  const model = Boolean(llm?.ready)
  const dot =
    py && model
      ? 'bg-good'
      : !py && !llm?.running && engine?.state === 'stopped'
        ? 'bg-[#4a4d55]'
        : 'bg-warn'
  const label = py && model ? 'ready' : dot === 'bg-warn' ? 'partly up' : 'off'

  return (
    <nav
      aria-label="App"
      className="relative flex w-21 shrink-0 flex-col items-center gap-1.5 border-r border-line bg-ink py-5"
    >
      <NavLink to="/" aria-label="Ohm home" className="pt-1">
        <img src={logo} alt="" className="size-8" />
      </NavLink>
      <span className="flex-1" />
      {LINKS.map(([to, label, Icon]) => (
        <NavLink
          key={to}
          to={to}
          end
          aria-label={label}
          title={label}
          className={({ isActive }) =>
            isActive ? `${tile} bg-accent text-white hover:bg-accent` : tile
          }
        >
          <Icon className="size-5" />
        </NavLink>
      ))}
      <span className="flex-[1.4]" />
      <button
        onClick={() => setOpen(!open)}
        aria-label={`Engine: ${label}`}
        aria-expanded={open}
        title="Engine"
        className={`${tile} relative cursor-pointer ${open ? 'bg-raised text-gray-100' : ''}`}
      >
        <LuPower className="size-5" />
        <span className={`absolute top-2.5 right-2.5 size-2 rounded-full ring-2 ring-ink ${dot}`} />
      </button>
      {open && <EnginePanel llm={llm} onClose={() => setOpen(false)} />}
    </nav>
  )
}

export default Sidebar
