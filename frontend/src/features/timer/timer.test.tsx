import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { Session } from '@/api/types'

import { formatClock, msUntilNextSecond } from './countdown'
import { fetchActiveSession, invalidateSessionState } from './session'
import { useAutoComplete, useCountdown, useDocumentTitle } from './useCountdown'

const BROWSER_NOW = Date.parse('2026-09-25T10:00:00Z')
// The browser clock runs two minutes slow relative to the server.
const SKEW_MS = 120_000

function session(overrides: Partial<Session> = {}): Session {
  return {
    id: 's1',
    task_id: 't1',
    kind: 'work',
    status: 'running',
    // Started one minute ago by the server's clock.
    started_at: new Date(BROWSER_NOW + SKEW_MS - 60_000).toISOString(),
    ended_at: null,
    planned_minutes: 25,
    duration_seconds: null,
    note: null,
    ...overrides,
  }
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

describe('countdown maths', () => {
  it('formats with a rounded-up second, and hours past sixty minutes', () => {
    expect(formatClock(25 * 60_000)).toBe('25:00')
    expect(formatClock(200)).toBe('00:01')
    expect(formatClock(0)).toBe('00:00')
    expect(formatClock(90 * 60_000)).toBe('1:30:00')
  })

  it('schedules the next tick on the second boundary', () => {
    expect(msUntilNextSecond(24_300)).toBe(310)
    expect(msUntilNextSecond(24_000)).toBe(1010)
    expect(msUntilNextSecond(0)).toBe(1000)
  })
})

describe('fetchActiveSession()', () => {
  it('stores server_now − Date.now() as the clock offset', async () => {
    vi.useFakeTimers({ now: BROWSER_NOW })
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        Response.json({ session: null, server_now: new Date(BROWSER_NOW + SKEW_MS).toISOString() }),
      ),
    )
    const data = await fetchActiveSession()
    expect(data.offsetMs).toBe(SKEW_MS)
    vi.useRealTimers()
  })
})

describe('useCountdown()', () => {
  beforeEach(() => vi.useFakeTimers({ now: BROWSER_NOW }))
  afterEach(() => vi.useRealTimers())

  it('applies the server offset to every reading', () => {
    const { result } = renderHook(() => useCountdown(session(), SKEW_MS), { wrapper })
    // 24:00, not the 26:00 an uncorrected (slow) browser clock would give.
    expect(result.current).toBe(24 * 60_000)
  })

  it('keeps pace with the clock as it ticks', async () => {
    const { result } = renderHook(() => useCountdown(session(), SKEW_MS), { wrapper })
    // Ticks land just after each second boundary, so allow for that.
    await act(() => vi.advanceTimersByTimeAsync(10_050))
    expect(formatClock(result.current!)).toBe('23:50')
  })

  it('is right immediately when a sleeping tab becomes visible, not a tick later', () => {
    const { result } = renderHook(() => useCountdown(session(), SKEW_MS), { wrapper })

    // Five minutes pass with no timer firing — a throttled background tab.
    vi.setSystemTime(BROWSER_NOW + 5 * 60_000)
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'))
    })
    expect(result.current).toBe(19 * 60_000)
  })

  it('does not spin on an unreadable timestamp', async () => {
    // A NaN delay is a 0 ms timer; rescheduling it forever pins a core.
    // Renders are batched, so count what gets scheduled instead.
    const schedule = vi.spyOn(window, 'setTimeout')
    renderHook(() => useCountdown(session({ started_at: 'not a date' }), SKEW_MS), { wrapper })
    await act(() => vi.advanceTimersByTimeAsync(1_000))
    expect(schedule.mock.calls.length).toBeLessThan(5)
  })

  it('returns null when nothing is running', () => {
    const { result } = renderHook(() => useCountdown(null, 0), { wrapper })
    expect(result.current).toBeNull()
  })
})

describe('useAutoComplete()', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  // The server's view: /active still has s1 running, on the skewed clock;
  // /complete succeeds. Tests override the parts they are about.
  const serverAnswers = (complete: () => Response = () => Response.json(session({ status: 'completed' }))) =>
    vi.fn(async (url: string) =>
      String(url).includes('/api/sessions/active')
        ? Response.json({ session: session(), server_now: new Date(Date.now() + SKEW_MS).toISOString() })
        : complete(),
    )

  beforeEach(() => {
    vi.useFakeTimers({ now: BROWSER_NOW })
    fetchMock = serverAnswers()
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => vi.useRealTimers())

  const completes = () =>
    fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/api/sessions/s1/complete'))

  it('claims completion exactly once when the countdown reaches zero', async () => {
    const s = session()
    renderHook(
      () => {
        const remaining = useCountdown(s, SKEW_MS)
        useAutoComplete(s, remaining)
      },
      { wrapper },
    )

    await act(() => vi.advanceTimersByTimeAsync(24 * 60_000 - 1_000))
    expect(completes()).toHaveLength(0)

    // Cross zero, then keep ticking: still a single claim.
    await act(() => vi.advanceTimersByTimeAsync(5_000))
    expect(completes()).toHaveLength(1)
  })

  it('does not claim again when a refetch hands back the same session anew', async () => {
    const s = session()
    const { rerender } = renderHook(
      ({ current }: { current: Session }) => {
        const remaining = useCountdown(current, SKEW_MS)
        useAutoComplete(current, remaining)
      },
      { wrapper, initialProps: { current: s } },
    )
    await act(() => vi.advanceTimersByTimeAsync(24 * 60_000 + 500))
    expect(completes()).toHaveLength(1)

    // The active-session query refetching before the completion lands.
    rerender({ current: { ...s } })
    await act(() => vi.advanceTimersByTimeAsync(2_000))
    expect(completes()).toHaveLength(1)
  })

  it('never completes early when the session and its offset arrive together', async () => {
    // Browser clock ten minutes fast; eighteen minutes into a 25-minute
    // session by the server's clock. Before the first fetch there is neither
    // a session nor an offset.
    const fast = -10 * 60_000
    vi.setSystemTime(BROWSER_NOW + 10 * 60_000)
    const s = session({ started_at: new Date(BROWSER_NOW - 18 * 60_000).toISOString() })
    type Props = { current: Session | null; offset: number }
    const { result, rerender } = renderHook<number | null, Props>(
      ({ current, offset }) => {
        const remaining = useCountdown(current, offset)
        useAutoComplete(current, remaining)
        return remaining
      },
      { wrapper, initialProps: { current: null, offset: 0 } },
    )

    await act(async () => rerender({ current: s, offset: fast }))
    expect(completes()).toHaveLength(0)
    expect(formatClock(result.current!)).toBe('07:00')
  })

  it('never claims on an unreadable timestamp', async () => {
    const s = session({ started_at: 'not a date' })
    renderHook(
      () => {
        const remaining = useCountdown(s, SKEW_MS)
        useAutoComplete(s, remaining)
      },
      { wrapper },
    )
    await act(() => vi.advanceTimersByTimeAsync(2_000))
    expect(completes()).toHaveLength(0)
  })

  it('does not silently claim a session found long overdue — the whole gap would count as focus', async () => {
    // Started last night, found at the next page load.
    const s = session({ started_at: new Date(BROWSER_NOW + SKEW_MS - 9 * 3600_000).toISOString() })
    renderHook(
      () => {
        const remaining = useCountdown(s, SKEW_MS)
        useAutoComplete(s, remaining)
      },
      { wrapper },
    )
    await act(() => vi.advanceTimersByTimeAsync(10_000))
    expect(completes()).toHaveLength(0)
  })

  it('still claims a session a throttled tab finds a few minutes overdue', async () => {
    const s = session({ started_at: new Date(BROWSER_NOW + SKEW_MS - 30 * 60_000).toISOString() })
    renderHook(
      () => {
        const remaining = useCountdown(s, SKEW_MS)
        useAutoComplete(s, remaining)
      },
      { wrapper },
    )
    await act(() => vi.advanceTimersByTimeAsync(1_000))
    expect(completes()).toHaveLength(1)
  })

  it('completes as soon as a tab that slept through the end wakes up', async () => {
    const s = session()
    renderHook(
      () => {
        const remaining = useCountdown(s, SKEW_MS)
        useAutoComplete(s, remaining)
      },
      { wrapper },
    )

    // Six minutes past the end: a throttled tab, not an abandoned session.
    vi.setSystemTime(BROWSER_NOW + 30 * 60_000)
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'))
    })
    expect(completes()).toHaveLength(1)
  })

  it('claims again if the session is still on screen at zero after a successful claim', async () => {
    // The POST worked but the refetch that would clear the timer failed:
    // without this the ring would sit at 00:00 until something else refetched.
    const s = session({ started_at: new Date(BROWSER_NOW + SKEW_MS - 25 * 60_000).toISOString() })
    renderHook(
      () => {
        const remaining = useCountdown(s, SKEW_MS)
        useAutoComplete(s, remaining)
      },
      { wrapper },
    )
    await act(() => vi.advanceTimersByTimeAsync(1_000))
    expect(completes()).toHaveLength(1)
    await act(() => vi.advanceTimersByTimeAsync(5_000))
    expect(completes()).toHaveLength(2)
  })

  it('checks the server clock before claiming — a browser clock that jumped would end it early', async () => {
    // Cached offset says time is up; the fresh server_now says ten minutes remain.
    const s = session()
    fetchMock.mockImplementation(async (url: string) =>
      String(url).includes('/api/sessions/active')
        ? Response.json({ session: s, server_now: new Date(BROWSER_NOW + SKEW_MS - 60_000 + 15 * 60_000).toISOString() })
        : Response.json(session({ status: 'completed' })),
    )
    renderHook(
      () => {
        const remaining = useCountdown(s, SKEW_MS + 24 * 60_000) // stale offset: already at zero
        useAutoComplete(s, remaining)
      },
      { wrapper },
    )
    await act(() => vi.advanceTimersByTimeAsync(1_000))
    expect(completes()).toHaveLength(0)
  })

  it('a claim that hangs is given up on and retried, not left stuck at 00:00', async () => {
    let completeCalls = 0
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      if (String(url).includes('/api/sessions/active')) {
        return Response.json({ session: session(), server_now: new Date(Date.now() + SKEW_MS).toISOString() })
      }
      completeCalls += 1
      if (completeCalls === 1) {
        // Hangs, as a stalled server would — until its deadline aborts it.
        return new Promise<Response>((_resolve, reject) =>
          init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError'))),
        )
      }
      return Response.json(session({ status: 'completed' }))
    })
    const s = session({ started_at: new Date(BROWSER_NOW + SKEW_MS - 25 * 60_000).toISOString() })
    renderHook(
      () => {
        const remaining = useCountdown(s, SKEW_MS)
        useAutoComplete(s, remaining)
      },
      { wrapper },
    )
    await act(() => vi.advanceTimersByTimeAsync(60_000))
    expect(completeCalls).toBeGreaterThanOrEqual(2)
  })

  it('retries after a failed claim, but not on every tick', async () => {
    fetchMock.mockImplementation(serverAnswers(() => new Response('', { status: 503 })))
    const s = session({ started_at: new Date(BROWSER_NOW + SKEW_MS - 25 * 60_000).toISOString() })
    renderHook(
      () => {
        const remaining = useCountdown(s, SKEW_MS)
        useAutoComplete(s, remaining)
      },
      { wrapper },
    )
    await act(() => vi.advanceTimersByTimeAsync(3_000))
    expect(completes()).toHaveLength(1)
    await act(() => vi.advanceTimersByTimeAsync(5_000))
    expect(completes()).toHaveLength(2)
  })
})

describe('useDocumentTitle()', () => {
  it('mirrors the remaining time into the tab title and restores it after', () => {
    type Props = { s: Session | null; r: number | null }
    const { rerender, unmount } = renderHook<void, Props>(
      ({ s, r }) => useDocumentTitle(s, r),
      { initialProps: { s: session(), r: 12 * 60_000 + 34_000 } },
    )
    expect(document.title).toBe('12:34 Focus – Pomodoro')

    rerender({ s: null, r: null })
    expect(document.title).toBe('Pomodoro')

    rerender({ s: session({ kind: 'short_break' }), r: 5_000 })
    expect(document.title).toBe('00:05 Short break – Pomodoro')
    unmount()
    expect(document.title).toBe('Pomodoro')
  })
})

describe('a failed completion claim', () => {
  it('rechecks the active session only — not the whole task list on every retry', async () => {
    vi.useFakeTimers({ now: BROWSER_NOW })
    const complete = vi.fn(() => new Response('', { status: 503 }))
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) =>
        String(url).includes('/api/sessions/active')
          ? Response.json({ session: session(), server_now: new Date(Date.now() + SKEW_MS).toISOString() })
          : complete(),
      ),
    )
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
    client.setQueryData(['tasks'], [])
    const s = session({ started_at: new Date(BROWSER_NOW + SKEW_MS - 25 * 60_000).toISOString() })
    renderHook(
      () => {
        const remaining = useCountdown(s, SKEW_MS)
        useAutoComplete(s, remaining)
      },
      { wrapper: ({ children }) => <QueryClientProvider client={client}>{children}</QueryClientProvider> },
    )
    await act(() => vi.advanceTimersByTimeAsync(1_000))
    expect(complete).toHaveBeenCalled() // the claim really was made, and failed
    expect(client.getQueryState(['tasks'])?.isInvalidated).toBe(false)
  })
})

describe('readActiveSessionFresh()', () => {
  it('does not join a read already in flight — its answer may predate the change being checked', async () => {
    const { readActiveSessionFresh } = await import('./session')
    let reads = 0
    let answerFirst!: (r: Response) => void
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, init?: RequestInit) => {
        reads += 1
        if (reads === 1) {
          return new Promise<Response>((resolve, reject) => {
            answerFirst = resolve
            init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
          })
        }
        return Response.json({ session: null, server_now: new Date().toISOString() })
      }),
    )
    const client = new QueryClient()
    // A refetch-on-focus, sent before another device completed the session.
    void client.fetchQuery({ queryKey: ['session', 'active'], queryFn: ({ signal }) => fetchActiveSession(signal) }).catch(() => null)
    await Promise.resolve()

    const fresh = await readActiveSessionFresh(client)
    void answerFirst
    expect(reads).toBe(2)
    expect(fresh.session).toBeNull()
  })
})

describe('invalidateSessionState()', () => {
  it('reaches every session list, including a task page’s history', async () => {
    const client = new QueryClient()
    const keys = [['session', 'active'], ['sessions', 'recent'], ['sessions', 'task', 't1'], ['tasks']]
    keys.forEach((key) => client.setQueryData(key, []))

    await invalidateSessionState(client)

    for (const key of keys) expect(client.getQueryState(key)?.isInvalidated, key.join('/')).toBe(true)
  })
})
