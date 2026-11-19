import { useProviders } from '../api/hooks'

// The only place that knows how provider ids map to the design's colour classes.
const CLASS: Record<string, string> = { mal: 'svc-mal', anilist: 'svc-al', kitsu: 'svc-k' }

/** Monogram chip (no provider logos). Name and monogram come from /api/providers. */
export function ProviderChip({ id, size = 40 }: { id: string; size?: number }) {
  const { data } = useProviders()
  const p = data?.find((x) => x.id === id)
  const fs = size <= 28 ? 11 : size <= 40 ? 13 : size <= 48 ? 15 : 17
  return (
    <span
      className={`svc ${CLASS[id] ?? ''}`}
      style={{ width: size, height: size, borderRadius: Math.max(6, Math.round(size * 0.3)), fontSize: fs }}
      title={p?.name}
      aria-hidden="true"
    >
      {p?.monogram ?? id.slice(0, 2).toUpperCase()}
    </span>
  )
}
