import { LuZap, LuStar, LuMoon, LuHeart, LuFlame, LuCrown, LuMusic, LuLeaf, LuPencil } from 'react-icons/lu'

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
const Art = ({ c, className }) => {
  const Icon = ICONS[c.icon] ?? LuStar
  return hasImage(c) ? (
    <img src={c.picture} alt="" className={`${className} object-cover`} />
  ) : (
    <Icon className={className} aria-hidden />
  )
}

// Playing-card corner index: initial over the icon, mirrored at the bottom.
const Corner = ({ c, flip = false }) => (
  <div
    aria-hidden
    className={`absolute flex flex-col items-center text-amber-700 ${flip ? 'right-2 bottom-1.5 rotate-180' : 'top-1.5 left-2'}`}
  >
    <span className="font-display text-xl leading-none">{c.name[0]}</span>
    <Art c={c} className="size-3.5 rounded-sm" />
  </div>
)

const CharacterCard = ({ c, editing, onPlay, onEdit }) => {
  const active = c.status === 'Active'
  return (
    <div className="flex flex-col gap-3">
      <article
        className={`aspect-[5/7] rounded-[14px] bg-card p-2.5 text-ink transition ${
          active
            ? 'shadow-[0_0_0_3px_#fbbf24,0_16px_32px_rgba(0,0,0,.45)]'
            : 'shadow-[0_12px_28px_rgba(0,0,0,.35)]'
        } ${editing ? '-translate-y-2 -rotate-[1.5deg]' : ''}`}
      >
        <div className="relative flex h-full flex-col gap-2.5 rounded-[9px] border-[1.5px] border-[#d6cfbf] px-3.5 py-10">
          <Corner c={c} />
          <Corner c={c} flip />
          <div className="relative grid flex-1 place-items-center rounded-md border border-[#e9d29c] bg-[#f6e7c4] text-amber-700">
            <Art c={c} className={hasImage(c) ? 'absolute inset-0 size-full rounded-md' : 'size-20'} />
            {active && (
              <span className="absolute -top-2.5 left-1/2 -translate-x-1/2 rounded-full bg-ink px-2.5 py-0.5 text-[10px] font-bold tracking-[0.12em] text-accent">
                IN PLAY
              </span>
            )}
          </div>
          <h2 className="truncate text-center font-display text-xl">{c.name}</h2>
          <p className="border-t border-dashed border-[#d6cfbf] pt-2 text-center font-mono text-[11px] text-[#5b5f68]">
            since {c.created}
          </p>
        </div>
      </article>
      <div className="flex gap-2">
        <button
          onClick={onPlay}
          disabled={active}
          className={`btn flex-1 ${active ? 'cursor-default bg-[#3a3528] text-accent' : 'bg-gray-100 text-ink'}`}
        >
          {active ? 'In play' : 'Play this card'}
        </button>
        <button
          onClick={onEdit}
          aria-label={`Edit ${c.name}`}
          aria-pressed={editing}
          className={`btn w-10 px-0 ${editing ? 'bg-gray-100 text-ink' : 'border border-line'}`}
        >
          <LuPencil />
        </button>
      </div>
    </div>
  )
}

export default CharacterCard
