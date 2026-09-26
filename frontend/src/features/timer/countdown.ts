import type { Session, SessionKind } from '@/api/types'

/** When the session is due to end, in server-clock epoch milliseconds. */
export function sessionEndsAt(session: Pick<Session, 'started_at' | 'planned_minutes'>): number {
  return Date.parse(session.started_at) + session.planned_minutes * 60_000
}

/** mm:ss, or h:mm:ss past an hour. Rounds up, so a fresh 25-minute session
 *  reads 25:00 and 00:00 appears exactly when time is up. */
export function formatClock(ms: number): string {
  if (!Number.isFinite(ms)) return '--:--'
  const total = Math.ceil(Math.max(0, ms) / 1000)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  const mm = String(m).padStart(2, '0')
  const ss = String(s).padStart(2, '0')
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`
}

/** Delay until the displayed second next changes, so the digits flip on the
 *  boundary instead of up to a second late. Always ≥ 1 frame. */
export function msUntilNextSecond(remaining: number): number {
  if (remaining <= 0) return 1000
  const intoSecond = remaining % 1000
  return (intoSecond === 0 ? 1000 : intoSecond) + 10
}

export const KIND_LABEL: Record<SessionKind, string> = {
  work: 'Focus',
  short_break: 'Short break',
  long_break: 'Long break',
}
