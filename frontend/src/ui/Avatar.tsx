import type { Me } from '../api/types'
import { safeImage } from './images'

/** Profile picture (hotlinked, round) or the initials tile when there is none. */
export function Avatar({ me, size = 36 }: { me: Pick<Me, 'display_name' | 'avatar_url'>; size?: number }) {
  const src = safeImage(me.avatar_url)
  if (src) {
    return (
      <img src={src} alt="" width={size} height={size} referrerPolicy="no-referrer"
        style={{ width: size, height: size, borderRadius: '50%', objectFit: 'cover', flex: 'none', background: 'var(--sc-high)' }} />
    )
  }
  return (
    <span className="avatar" style={{ width: size, height: size, fontSize: Math.round(size * 0.42) }} aria-hidden="true">
      {(me.display_name.trim()[0] ?? '?').toUpperCase()}
    </span>
  )
}
