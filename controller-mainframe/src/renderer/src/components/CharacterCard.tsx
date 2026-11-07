import { LuZap, LuStar, LuMoon, LuHeart, LuFlame, LuCrown, LuMusic, LuLeaf } from 'react-icons/lu'

export const ICONS = {
  star: LuStar,
  zap: LuZap,
  moon: LuMoon,
  heart: LuHeart,
  flame: LuFlame,
  crown: LuCrown,
  music: LuMusic,
  leaf: LuLeaf
}
export const hasImage = (c) => c.picture?.startsWith('data:')

// The character's uploaded picture, or its chosen icon.
export const Art = ({ c, className }) => {
  const Icon = ICONS[c.icon] ?? LuStar
  return hasImage(c) ? (
    <img src={c.picture} alt="" className={`${className} object-cover`} />
  ) : (
    <Icon className={className} aria-hidden />
  )
}

// One card in the deck. `model` is what it runs on, already named ("Gemma 4 E4B + ohm-v3").
const CharacterCard = ({ c, model, editing, onClick }) => {
  const active = c.status === 'Active'
  return (
    <button
      onClick={onClick}
      aria-pressed={editing}
      className="group flex cursor-pointer flex-col text-left"
    >
      <span
        className={`relative flex aspect-[5/7] flex-col justify-between rounded-[10px] border bg-panel p-3.5 ${
          active
            ? 'border-accent'
            : editing
              ? 'border-[#8a8d95]'
              : 'border-edge group-hover:border-[#4a4d55]'
        }`}
      >
        <span className="flex items-center gap-2">
          <span className={`text-[15px] font-semibold ${active ? 'text-clay' : 'text-soft'}`}>
            {c.name[0]}
          </span>
          <span className="flex-1" />
          {active && (
            <span className="rounded bg-accent px-1.5 py-1 text-[10.5px] leading-none font-semibold tracking-wider text-white">
              IN PLAY
            </span>
          )}
        </span>
        <span
          className={`relative grid size-18 place-items-center self-center overflow-hidden rounded-full border border-edge ${active ? 'text-clay' : 'text-soft'}`}
        >
          <Art c={c} className={hasImage(c) ? 'absolute inset-0 size-full' : 'size-7'} />
        </span>
        <span className="flex flex-col gap-1.5">
          <span className="truncate text-base font-semibold">{c.name}</span>
          <span className="font-mono text-[11px] text-muted">since {c.created}</span>
          <span className="truncate font-mono text-[11px] text-soft">{model}</span>
        </span>
      </span>
    </button>
  )
}

export default CharacterCard
