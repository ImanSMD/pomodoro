import { useIsMutating } from '@tanstack/react-query'
import { toast } from 'sonner'

import type { SessionKind } from '@/api/types'
import { toastError } from '@/lib/toastError'

import { startSessionKey, useStartSession } from './session'

/** Bring the timer into view on layouts where it can be scrolled away. */
function revealTimer() {
  const stage = document.getElementById('timer')
  if (!stage) return
  const { top, bottom } = stage.getBoundingClientRect()
  if (top >= 0 && bottom <= window.innerHeight) return
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  stage.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' })
}

export function useStartTask() {
  const mutation = useStartSession()

  function start(taskId: string, kind: SessionKind = 'work') {
    mutation.mutate(
      { taskId, kind },
      {
        onSuccess: (result) => {
          if (!result.started) {
            // 409: something is already running. Not a failure to the user —
            // the refetch has put that session on the timer, so point at it.
            toast('A session is already running', {
              description: 'It’s on the timer now. Finish or discard it to start another.',
            })
          }
          revealTimer()
        },
        onError: toastError('Couldn’t start the timer'),
      },
    )
  }

  return {
    start,
    // Counted across every instance, so a start from one row disables the
    // Start buttons on all of them until it lands.
    starting: useIsMutating({ mutationKey: startSessionKey }) > 0,
  }
}
