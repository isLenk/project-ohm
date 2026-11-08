import { useState } from 'react'
import { LuCheck } from 'react-icons/lu'
import { color, cx } from './util'

type Who = { name: string; display_name: string | null; avatar_url?: string | null; color_index?: number }

// URLs that failed once (deleted avatar, offline). Remembered so virtualized rows don't retry and flicker.
const broken = new Set<string>()

// Discord's CDN serves powers of two from 16 to 4096. Ask for 2x the drawn size instead of the export's 512.
function sized(url: string, px: number) {
  if (!url.includes('cdn.discordapp.com')) return url
  const want = Math.min(4096, Math.max(16, 2 ** Math.ceil(Math.log2(px * 2))))
  const u = new URL(url)
  u.searchParams.set('size', String(want))
  return u.toString()
}

// A person's icon: their Discord avatar ringed in their color, or a colored initial if there is no avatar.
// colorIndex overrides who.color_index (blocks carry their own copy); tint overrides both (the main app
// colors people by id, not by a Curator color index).
export function Avatar({ who, colorIndex, tint, size, className }: { who: Who | undefined; colorIndex?: number; tint?: string; size: number; className?: string }) {
  const c = tint ?? color(colorIndex ?? who?.color_index)
  const url = who?.avatar_url || null
  const [failedUrl, setFailedUrl] = useState<string | null>(null)
  const failed = !!url && (failedUrl === url || broken.has(url))
  const ring = size >= 24 ? 2 : 1.5
  const box = { width: size, height: size, boxShadow: `0 0 0 ${ring}px ${c}` }

  if (url && !failed) {
    return (
      <img
        src={sized(url, size)}
        alt=""
        loading="lazy"
        draggable={false}
        onError={() => {
          broken.add(url)
          setFailedUrl(url)
        }}
        className={cx('shrink-0 rounded-full bg-raised object-cover', className)}
        style={box}
      />
    )
  }
  const initial = (who?.display_name || who?.name || '?').slice(0, 1).toUpperCase()
  return (
    <span
      className={cx('grid shrink-0 place-items-center rounded-full font-bold text-ink', className)}
      style={{ width: size, height: size, background: c, fontSize: Math.round(size * 0.4) }}
    >
      {size >= 16 ? initial : null}
    </span>
  )
}

// Discord's tag for bot accounts, so bots read as bots at a glance.
export function BotTag({ className }: { className?: string }) {
  return (
    <span
      title="Bot account. Its messages are left out of training."
      className={cx(
        'inline-flex h-[15px] shrink-0 items-center gap-0.5 self-center rounded-[3px] bg-[#5865F2] px-1 text-[10px] leading-none font-semibold text-white',
        className
      )}
    >
      <LuCheck className="size-2.5" strokeWidth={3.5} />
      APP
    </span>
  )
}
