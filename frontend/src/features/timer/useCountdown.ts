import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'

import type { Session } from '@/api/types'

import { formatClock, KIND_LABEL, msUntilNextSecond, sessionEndsAt } from './countdown'
import { readActiveSessionFresh, useEndSession } from './session'

/**
 * Milliseconds left on `session`, re-derived from its start time on every
 * tick using the server-corrected clock — negative once it is overdue, so
 * callers can tell "just ended" from "ended hours ago". null when nothing is
 * running.
 */
export function useCountdown(session: Session | null, offsetMs: number): number | null {
  const endsAt = session ? sessionEndsAt(session) : null
  // The state only schedules re-renders; the clock itself is read during
  // render. Holding a stored reading instead would let the render in which a
  // session and its offset first arrive use a reading taken before the offset
  // was known — on a fast browser clock that is "time's up", and the
  // completion claim fires minutes early.
  const [, setTick] = useState(0)

  useEffect(() => {
    // An unreadable timestamp makes every delay NaN — a 0 ms timer that would
    // reschedule itself forever.
    if (endsAt === null || !Number.isFinite(endsAt) || !Number.isFinite(offsetMs)) return
    let timer: number | undefined

    const tick = () => {
      const now = Date.now() + offsetMs
      setTick((n) => n + 1)
      timer = window.setTimeout(tick, msUntilNextSecond(endsAt - now))
    }
    // A throttled background tab can fire its timer a minute late. Recompute
    // the moment it is visible again rather than waiting for that.
    const onVisible = () => {
      if (document.visibilityState !== 'visible') return
      window.clearTimeout(timer)
      tick()
    }

    tick()
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.clearTimeout(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [endsAt, offsetMs])

  return endsAt === null ? null : endsAt - (Date.now() + offsetMs)
}

const COMPLETE_RETRY_MS = 5_000

/**
 * How far past its end a session may be and still be claimed without asking.
 * Enough for a throttled or briefly sleeping tab. Beyond it the session was
 * most likely abandoned — every tab closed overnight — and completing it would
 * record the whole gap as focus, since the endpoint caps nothing. The timer
 * asks instead (see OverdueSession).
 */
export const OVERDUE_CLAIM_LIMIT_MS = 15 * 60_000

export function isLongOverdue(remaining: number | null): boolean {
  return remaining !== null && remaining < -OVERDUE_CLAIM_LIMIT_MS
}

/**
 * Claim completion when the countdown reaches zero. The endpoint is
 * idempotent and computes the duration from its own timestamps, so a second
 * tab racing this one records the same session, and a throttled tab waking a
 * little late records what actually elapsed. A session found long overdue is
 * left for the user to settle — see OVERDUE_CLAIM_LIMIT_MS.
 */
export function useAutoComplete(session: Session | null, remaining: number | null): void {
  const { mutateAsync } = useEndSession()
  const queryClient = useQueryClient()
  const claimed = useRef<string | null>(null)
  // Bumped to re-run the effect after a claim. Once the countdown sits at zero
  // nothing else changes, so without it a retry would never happen.
  const [attempt, setAttempt] = useState(0)
  const recheck = useRef<number | undefined>(undefined)

  useEffect(() => () => window.clearTimeout(recheck.current), [])

  useEffect(() => {
    if (!session || session.status !== 'running' || remaining === null || remaining > 0) return
    // An unreadable timestamp gives NaN, and NaN > 0 is false: without this
    // it would claim the session the moment it appeared.
    if (!Number.isFinite(remaining)) return
    if (isLongOverdue(remaining)) return
    if (claimed.current === session.id) return

    claimed.current = session.id
    const id = session.id
    const endsAt = sessionEndsAt(session)

    const claim = async (): Promise<'claimed' | 'early' | 'gone'> => {
      // Zero by the cached offset is not zero by the server: the browser
      // clock may have jumped since (NTP correcting after sleep). Re-read
      // server_now first — this also refreshes the offset the countdown uses.
      const fresh = await readActiveSessionFresh(queryClient)
      if (fresh.session?.id !== id) return 'gone'
      if (endsAt - (Date.now() + fresh.offsetMs) > 0) return 'early'
      await mutateAsync({ sessionId: id, action: 'complete' })
      return 'claimed'
    }

    // Check back later, whatever happened — not on every tick, so a server
    // that is down is not hit once a second. After a failure that is the
    // retry. After a success it matters too: if the refetch that should clear
    // this session failed, it is still on screen at 00:00, and the idempotent
    // claim goes again and brings another refetch with it.
    const rearm = () => {
      window.clearTimeout(recheck.current)
      recheck.current = window.setTimeout(() => {
        if (claimed.current !== id) return
        claimed.current = null
        setAttempt((n) => n + 1)
      }, COMPLETE_RETRY_MS)
    }

    claim().then(
      (outcome) => {
        // Early: the countdown re-renders on the corrected offset and will
        // cross zero again, for real this time.
        if (outcome === 'early') claimed.current = null
        else rearm()
      },
      rearm,
    )
  }, [session, remaining, mutateAsync, queryClient, attempt])
}

const IDLE_TITLE = 'Pomodoro'

/** Keeps the countdown readable from a background tab's title. */
export function useDocumentTitle(session: Session | null, remaining: number | null): void {
  const label = session ? KIND_LABEL[session.kind] : null
  const clock = remaining === null ? null : formatClock(remaining)

  useEffect(() => {
    document.title = clock && label ? `${clock} ${label} – ${IDLE_TITLE}` : IDLE_TITLE
  }, [clock, label])

  useEffect(() => () => void (document.title = IDLE_TITLE), [])
}
