import { useCallback, useMemo, useSyncExternalStore } from 'react'

export function useMediaQuery(query: string): boolean {
  // One MediaQueryList per query, and a stable subscribe, or every render
  // would parse a new list and useSyncExternalStore would resubscribe.
  const list = useMemo(() => window.matchMedia(query), [query])
  const subscribe = useCallback(
    (onChange: () => void) => {
      list.addEventListener('change', onChange)
      return () => list.removeEventListener('change', onChange)
    },
    [list],
  )
  return useSyncExternalStore(subscribe, () => list.matches)
}
