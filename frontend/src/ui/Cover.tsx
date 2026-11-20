import { useMe } from '../api/hooks'
import { Icon } from './Icon'
import { safeImage } from './images'

/**
 * Anime cover, hotlinked from the provider (never re-hosted); a placeholder tile when missing or not an allowed host.
 * Covers of adult titles are blurred unless the user chose to see them (Settings), and marked 18+.
 */
export function Cover({ title, url, w = 48, h = 68, adult = false }: { title: string; url: string | null; w?: number; h?: number; adult?: boolean }) {
  const show = useMe().data?.show_adult_covers ?? false
  const hidden = adult && !show
  const src = safeImage(url)
  return (
    <span className="cover" style={{ width: w, height: h }} role="img" aria-label={hidden ? 'Cover hidden: adult title' : `Cover: ${title}`}>
      {src && !hidden ? (
        <img src={src} alt="" loading="lazy" decoding="async" referrerPolicy="no-referrer" />
      ) : src ? (
        <img src={src} alt="" loading="lazy" decoding="async" referrerPolicy="no-referrer" style={{ filter: 'blur(10px) brightness(.8)', transform: 'scale(1.2)' }} />
      ) : (
        <Icon name="image" size={Math.min(24, w / 2)} stroke={1.6} />
      )}
      {hidden && (
        <span className="label-m" style={{ position: 'absolute', inset: 'auto 0 4px 0', textAlign: 'center', color: '#fff', textShadow: '0 1px 2px rgba(0,0,0,.8)' }}>18+</span>
      )}
    </span>
  )
}
