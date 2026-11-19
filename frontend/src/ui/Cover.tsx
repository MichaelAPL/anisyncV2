import { Icon } from './Icon'
import { safeImage } from './images'

/** Anime cover, hotlinked from the provider (never re-hosted); a placeholder tile when missing or not an allowed host. */
export function Cover({ title, url, w = 48, h = 68 }: { title: string; url: string | null; w?: number; h?: number }) {
  const src = safeImage(url)
  return (
    <span className="cover" style={{ width: w, height: h }} role="img" aria-label={`Cover: ${title}`}>
      {src ? <img src={src} alt="" loading="lazy" decoding="async" referrerPolicy="no-referrer" /> : <Icon name="image" size={Math.min(24, w / 2)} stroke={1.6} />}
    </span>
  )
}
