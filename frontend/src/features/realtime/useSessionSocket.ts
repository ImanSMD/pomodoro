import { useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'

import { activeSessionKey, invalidateSessionState, sessionListsKey } from '@/features/timer/session'

import { startSessionSocket } from './sessionSocket'

/**
 * Live session updates from this user's other tabs and devices. Each event
 * invalidates rather than patches the cache: the refetch of the active
 * session carries a fresh server_now, and with it a fresh clock offset.
 */
export function useSessionSocket(): void {
  const queryClient = useQueryClient()

  useEffect(
    () =>
      startSessionSocket({
        onEvent: () => void invalidateSessionState(queryClient),
        // Catch-up for events missed while there was no socket. Session state
        // only: the task list is the costly query, and an open happens on
        // every token expiry. A running task this tab has never loaded is
        // fetched on demand by the timer instead (useSessionTask).
        onOpen: () => {
          void queryClient.invalidateQueries({ queryKey: activeSessionKey })
          void queryClient.invalidateQueries({ queryKey: sessionListsKey })
        },
      }),
    [queryClient],
  )
}
