import { NavLink } from 'react-router-dom'
import {
  LuLayoutGrid,
  LuUsers,
  LuLayers,
  LuDatabase,
  LuFilter,
  LuSlidersHorizontal,
  LuMessagesSquare
} from 'react-icons/lu'

const LINKS = [
  ['/', 'Dashboard', LuLayoutGrid],
  ['/characters', 'Characters', LuUsers],
  ['/curator', 'Curator', LuMessagesSquare],
  ['/modules', 'Modules', LuLayers],
  ['/memories', 'Memories', LuDatabase],
  ['/filters', 'Filters', LuFilter],
  ['/settings', 'Settings', LuSlidersHorizontal]
] as const

const Sidebar = () => (
  <nav className="flex w-56 shrink-0 flex-col gap-7 border-r border-[#34353d] bg-ink px-4 py-6">
    <NavLink to="/" className="flex items-center gap-3 px-2">
      <span className="grid size-9 place-items-center rounded-lg bg-accent font-display text-xl text-ink">
        Ω
      </span>
      <span className="flex flex-col leading-tight">
        <span className="font-display text-lg tracking-[0.12em]">OHM</span>
        <span className="font-mono text-[11px] tracking-widest text-muted">INTERFACE</span>
      </span>
    </NavLink>
    <div className="flex flex-col gap-1">
      {LINKS.map(([to, label, Icon]) => (
        <NavLink
          key={to}
          to={to}
          end
          className={({ isActive }) =>
            `group flex min-h-11 items-center gap-3 rounded-lg px-3 text-sm font-medium ${
              isActive ? 'active bg-raised text-gray-100' : 'text-muted hover:text-gray-100'
            }`
          }
        >
          <Icon className="size-[18px] group-[.active]:text-accent" />
          {label}
        </NavLink>
      ))}
    </div>
  </nav>
)

export default Sidebar
