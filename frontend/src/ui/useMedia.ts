import { useSyncExternalStore } from 'react'

/** Live `matchMedia` result. Phone layout in the stylesheet starts below 600px. */
export function useMedia(query: string): boolean {
  return useSyncExternalStore(
    (cb) => {
      const m = matchMedia(query)
      m.addEventListener('change', cb)
      return () => m.removeEventListener('change', cb)
    },
    () => matchMedia(query).matches,
    () => false,
  )
}

export const useIsPhone = () => useMedia('(max-width: 599px)')
