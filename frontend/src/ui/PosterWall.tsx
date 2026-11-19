import posters from '../data/posters.json'
import { safeImage } from './images'

interface Poster { id: number; title: string; cover: string; color: string | null }

const COLUMNS = 15 // enough to fill very wide screens; the extra columns are cropped on narrower ones
const PER_COLUMN = 5
const ALL = (posters as Poster[]).filter((p) => safeImage(p.cover))

function Column({ index }: { index: number }) {
  // Spread the list across columns so neighbours differ, then repeat the set so the loop has no seam.
  const items = Array.from({ length: PER_COLUMN }, (_, k) => ALL[(index * PER_COLUMN + k) % ALL.length])
  const seconds = 150 + (index % 9) * 22
  return (
    <div style={{ flex: 'none', width: 150, overflow: 'hidden' }}>
      <div
        className="wall-track"
        style={{ animation: `wall-${index % 2 ? 'down' : 'up'} ${seconds}s linear infinite`, marginTop: -(index % 4) * 70 }}
      >
        {[0, 1].flatMap((copy) =>
          items.map((p) => (
            <img
              key={`${copy}-${p.id}`}
              src={safeImage(p.cover)!}
              alt=""
              loading="lazy"
              decoding="async"
              referrerPolicy="no-referrer"
              style={{ flex: 'none', width: 150, height: 225, objectFit: 'cover', borderRadius: 16, background: p.color ?? 'var(--sc-high)' }}
            />
          )),
        )}
      </div>
    </div>
  )
}

/** Decorative wall of drifting posters. Hidden from assistive tech; motion stops with prefers-reduced-motion. */
export function PosterWall() {
  if (ALL.length === 0) return null
  return (
    <>
      <div aria-hidden="true" style={{ position: 'absolute', inset: 0, display: 'flex', justifyContent: 'center', gap: 14, overflow: 'hidden' }}>
        {Array.from({ length: COLUMNS }, (_, c) => <Column key={c} index={c} />)}
      </div>
      {/* Surface-coloured scrim keeps the card readable in light and dark. */}
      <div
        aria-hidden="true"
        style={{
          position: 'absolute', inset: 0,
          background:
            'linear-gradient(90deg, color-mix(in srgb, var(--surface) 94%, transparent) 0%, color-mix(in srgb, var(--surface) 72%, transparent) 55%, color-mix(in srgb, var(--surface) 55%, transparent) 100%)',
        }}
      />
      <div aria-hidden="true" style={{ position: 'absolute', left: 0, right: 0, bottom: 0, height: 120, background: 'linear-gradient(transparent, var(--surface))' }} />
    </>
  )
}
