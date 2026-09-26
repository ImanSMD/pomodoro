import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'

import { api, ApiError } from '@/api/client'
import type { ActiveSessionResponse, Session, SessionKind } from '@/api/types'
import { tasksKey } from '@/features/tasks/api'

export const activeSessionKey = ['session', 'active'] as const
// Every session list lives under ['sessions', ...] (today, a task's history),
// so one prefix invalidation reaches them all.
export const sessionListsKey = ['sessions'] as const
export const startSessionKey = ['session', 'start'] as const

export interface ActiveSessionData {
  session: Session | null
  /** server clock − browser clock, in ms. Add it to Date.now() before every
   *  reading: the browser's clock is not trustworthy, and a countdown built on
   *  a skewed one ends early, late, or never. */
  offsetMs: number
}

// Session reads and writes that a claim waits on get a deadline, so a
// stalled server cannot leave the timer stuck at 00:00 with nothing retrying.
const SESSION_REQUEST_TIMEOUT_MS = 20_000

export async function fetchActiveSession(signal?: AbortSignal): Promise<ActiveSessionData> {
  const body = await api<ActiveSessionResponse>('/api/sessions/active', {
    signal,
    timeoutMs: SESSION_REQUEST_TIMEOUT_MS,
  })
  // Taken once per fetch, at receipt. The error is the response's one-way
  // latency — milliseconds, against a clock that can be minutes off.
  const offsetMs = Date.parse(body.server_now) - Date.now()
  return { session: body.session, offsetMs }
}

export function useActiveSession() {
  return useQuery({
    queryKey: activeSessionKey,
    queryFn: ({ signal }) => fetchActiveSession(signal),
  })
}

/**
 * The server's answer as of now. A read already in flight is cancelled rather
 * than joined: it may have been sent before the very change being checked
 * for, and deciding on it is how a stale screen overwrites a finished session.
 */
export async function readActiveSessionFresh(queryClient: QueryClient): Promise<ActiveSessionData> {
  await queryClient.cancelQueries({ queryKey: activeSessionKey })
  return queryClient.fetchQuery({
    queryKey: activeSessionKey,
    queryFn: ({ signal }) => fetchActiveSession(signal),
    staleTime: 0,
  })
}

/** Everything a session change can affect. The active-session refetch also
 *  refreshes the clock offset, which is why nothing writes that cache
 *  directly from a mutation response: the response has no server_now. */
export function invalidateSessionState(queryClient: QueryClient) {
  // Default cancelRefetch on purpose: a refetch already in flight may have
  // been sent before the change it is meant to show, and joining it would
  // cache that stale answer as fresh. A mutation and its socket echo cost a
  // duplicate request; a timer stuck on a finished session costs more.
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: activeSessionKey }),
    queryClient.invalidateQueries({ queryKey: sessionListsKey }),
    queryClient.invalidateQueries({ queryKey: tasksKey }),
  ])
}

export type StartResult = { started: true; session: Session } | { started: false; conflict: true }

export function useStartSession() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationKey: startSessionKey,
    mutationFn: async (vars: { taskId: string; kind: SessionKind }): Promise<StartResult> => {
      try {
        const session = await api<Session>('/api/sessions/start', {
          method: 'POST',
          body: { task_id: vars.taskId, kind: vars.kind },
        })
        return { started: true, session }
      } catch (error) {
        // One is already running (another tab, another device, a double
        // click). Not an error from the user's point of view: the refetch
        // below puts the running session on screen.
        if (error instanceof ApiError && error.status === 409) {
          return { started: false, conflict: true }
        }
        throw error
      }
    },
    // Not returned, so not awaited: the Start buttons re-enable once the
    // session is started, not once the whole task list has reloaded.
    onSettled: () => void invalidateSessionState(queryClient),
  })
}

export type EndAction =
  | { sessionId: string; action: 'complete' | 'cancel' }
  /** fix-end: close it as having run exactly `minutes`. */
  | { sessionId: string; action: 'record'; minutes: number }

export function useEndSession() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (vars: EndAction) =>
      vars.action === 'record'
        ? api<Session>(`/api/sessions/${vars.sessionId}`, {
            method: 'PATCH',
            body: { duration_minutes: vars.minutes },
          })
        : api<Session>(`/api/sessions/${vars.sessionId}/${vars.action}`, {
            method: 'POST',
            timeoutMs: SESSION_REQUEST_TIMEOUT_MS,
          }),
    onSuccess: () => invalidateSessionState(queryClient),
    // A failure changed nothing; recheck only whether the session is still
    // running. Refetching the task list too would repeat on every retry.
    onError: () => queryClient.invalidateQueries({ queryKey: activeSessionKey }),
  })
}
