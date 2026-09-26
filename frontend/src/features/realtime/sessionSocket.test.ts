import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import * as client from '@/api/client'

import { backoffDelay, parseSessionEvent, startSessionSocket } from './sessionSocket'

const NOW = Date.parse('2026-09-25T10:00:00Z')

class FakeSocket {
  static instances: FakeSocket[] = []
  onopen: (() => void) | null = null
  onmessage: ((m: { data: unknown }) => void) | null = null
  onclose: (() => void) | null = null
  closed = false

  constructor(readonly url: string) {
    FakeSocket.instances.push(this)
  }
  open() {
    this.onopen?.()
  }
  receive(data: unknown) {
    this.onmessage?.({ data })
  }
  close() {
    if (this.closed) return
    this.closed = true
    this.onclose?.()
  }
}

function jwt(expSeconds: number): string {
  const payload = btoa(JSON.stringify({ sub: 'u', exp: expSeconds })).replace(/=+$/, '')
  return `h.${payload}.s`
}

const live = () => FakeSocket.instances.filter((s) => !s.closed)
const latest = () => FakeSocket.instances.at(-1)!

beforeEach(() => {
  vi.useFakeTimers({ now: NOW })
  FakeSocket.instances = []
  vi.stubGlobal('WebSocket', FakeSocket)
  // Jitter pinned to the midpoint, so delays are exactly 500, 1000, 2000…
  vi.spyOn(Math, 'random').mockReturnValue(0.5)
  client.setAccessToken(jwt(NOW / 1000 + 15 * 60))
  // Default: a refresh succeeds and hands back the token already held.
  vi.spyOn(client, 'refreshAccessToken').mockImplementation(async () => client.getAccessToken())
})

afterEach(() => {
  client.setAccessToken(null)
  vi.useRealTimers()
})

describe('backoffDelay()', () => {
  it('doubles from 500ms and caps at 30s, with ±25% jitter', () => {
    const mid = () => 0.5
    expect([0, 1, 2, 3].map((n) => backoffDelay(n, mid))).toEqual([500, 1000, 2000, 4000])
    expect(backoffDelay(20, mid)).toBe(30_000)
    expect(backoffDelay(0, () => 0)).toBe(375)
    expect(backoffDelay(0, () => 1)).toBe(625)
  })
})

describe('parseSessionEvent()', () => {
  it('accepts the three session events and rejects anything else', () => {
    expect(parseSessionEvent('{"event":"session.started","data":{}}')?.event).toBe(
      'session.started',
    )
    expect(parseSessionEvent('{"event":"user.deleted","data":{}}')).toBeNull()
    expect(parseSessionEvent('not json')).toBeNull()
    expect(parseSessionEvent(new Blob())).toBeNull()
  })
})

describe('startSessionSocket()', () => {
  it('connects with the current access token and forwards session events', async () => {
    const onEvent = vi.fn()
    const stop = startSessionSocket({ onEvent, onOpen: vi.fn() })
    await vi.advanceTimersByTimeAsync(0)

    expect(latest().url).toBe(
      `ws://api.test/ws?token=${encodeURIComponent(client.getAccessToken()!)}`,
    )
    latest().open()
    latest().receive('{"event":"session.completed","data":{"id":"s1"}}')
    latest().receive('garbage')
    expect(onEvent).toHaveBeenCalledTimes(1)
    expect(onEvent.mock.calls[0][0]).toMatchObject({ event: 'session.completed' })
    stop()
  })

  it('reconnects with growing backoff, and reports every open', async () => {
    const onOpen = vi.fn()
    const stop = startSessionSocket({ onEvent: vi.fn(), onOpen })
    await vi.advanceTimersByTimeAsync(0)
    latest().open()
    // Even the first: an event may have fired between the page's first fetch
    // and this socket existing.
    expect(onOpen).toHaveBeenCalledTimes(1)

    latest().close()
    await vi.advanceTimersByTimeAsync(499)
    expect(FakeSocket.instances).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(FakeSocket.instances).toHaveLength(2)

    // Fails before opening: the next wait doubles.
    latest().close()
    await vi.advanceTimersByTimeAsync(999)
    expect(FakeSocket.instances).toHaveLength(2)
    await vi.advanceTimersByTimeAsync(1)
    expect(FakeSocket.instances).toHaveLength(3)

    latest().open()
    expect(onOpen).toHaveBeenCalledTimes(2)
    stop()
  })

  it('refreshes after a handshake is refused, even if the browser clock says the token is fine', async () => {
    // Browser clock behind the server: the server has expired this token,
    // but by local time it still has minutes left.
    const refresh = vi.spyOn(client, 'refreshAccessToken')
    const fresh = jwt(NOW / 1000 + 30 * 60)
    refresh.mockImplementation(async () => {
      client.setAccessToken(fresh)
      return fresh
    })

    const stop = startSessionSocket({ onEvent: vi.fn(), onOpen: vi.fn() })
    await vi.advanceTimersByTimeAsync(0)
    expect(refresh).not.toHaveBeenCalled()

    latest().close() // refused before it ever opened
    await vi.advanceTimersByTimeAsync(500)
    expect(refresh).toHaveBeenCalledTimes(1)
    expect(latest().url).toContain(encodeURIComponent(fresh))
    stop()
  })

  it('refreshes an expiring token before reconnecting', async () => {
    const refresh = vi.spyOn(client, 'refreshAccessToken')
    const fresh = jwt(NOW / 1000 + 30 * 60)
    refresh.mockImplementation(async () => {
      client.setAccessToken(fresh)
      return fresh
    })

    const stop = startSessionSocket({ onEvent: vi.fn(), onOpen: vi.fn() })
    await vi.advanceTimersByTimeAsync(0)
    latest().open()
    expect(refresh).not.toHaveBeenCalled()

    // Fifteen minutes on, the server closes the socket as its token expires.
    await vi.advanceTimersByTimeAsync(15 * 60_000)
    latest().close()
    await vi.advanceTimersByTimeAsync(500)

    expect(refresh).toHaveBeenCalledTimes(1)
    expect(latest().url).toContain(encodeURIComponent(fresh))
    stop()
  })

  it('forces a refresh again on a later token cycle once a forced one has worked', async () => {
    // Skewed clock, two token lifetimes: each server-side expiry is refused
    // while the local clock still thinks the token is good.
    let n = 0
    const refresh = vi.spyOn(client, 'refreshAccessToken').mockImplementation(async () => {
      const t = jwt(NOW / 1000 + 15 * 60 + ++n)
      client.setAccessToken(t)
      return t
    })
    const stop = startSessionSocket({ onEvent: vi.fn(), onOpen: vi.fn() })
    await vi.advanceTimersByTimeAsync(0)

    // Cycle 1: the first handshake is refused; a forced refresh fixes it.
    latest().close()
    await vi.advanceTimersByTimeAsync(1_000)
    expect(refresh).toHaveBeenCalledTimes(1)
    latest().open()
    await vi.advanceTimersByTimeAsync(60_000)

    // Cycle 2: the server ends the open socket at expiry; the reconnect,
    // still judged valid by the slow local clock, is refused.
    latest().close()
    await vi.advanceTimersByTimeAsync(1_000)
    latest().close()
    await vi.advanceTimersByTimeAsync(2_000)
    expect(refresh).toHaveBeenCalledTimes(2)
    stop()
  })

  it('does not rotate the cookie on every retry when handshakes fail for other reasons', async () => {
    // /ws unreachable (a proxy without Upgrade, a network blip): the fresh
    // token is refused too, so refreshing again would only churn the cookie.
    let n = 0
    const refresh = vi.spyOn(client, 'refreshAccessToken').mockImplementation(async () => {
      const t = jwt(NOW / 1000 + 15 * 60 + ++n)
      client.setAccessToken(t)
      return t
    })

    const stop = startSessionSocket({ onEvent: vi.fn(), onOpen: vi.fn() })
    await vi.advanceTimersByTimeAsync(0)
    for (let i = 0; i < 5; i++) {
      latest().close()
      await vi.advanceTimersByTimeAsync(30_000)
    }
    expect(FakeSocket.instances.length).toBeGreaterThanOrEqual(5)
    expect(refresh).toHaveBeenCalledTimes(1)
    stop()
  })

  it('gives up quietly when the refresh says the user is logged out', async () => {
    client.setAccessToken(null)
    vi.spyOn(client, 'refreshAccessToken').mockResolvedValue(null)

    const stop = startSessionSocket({ onEvent: vi.fn(), onOpen: vi.fn() })
    await vi.advanceTimersByTimeAsync(60_000)

    expect(FakeSocket.instances).toHaveLength(0)
    stop()
  })

  it('stop() closes the socket and nothing reconnects afterwards', async () => {
    const stop = startSessionSocket({ onEvent: vi.fn(), onOpen: vi.fn() })
    await vi.advanceTimersByTimeAsync(0)
    latest().open()

    stop()
    await vi.advanceTimersByTimeAsync(60_000)
    expect(live()).toHaveLength(0)
    expect(FakeSocket.instances).toHaveLength(1)
  })

  it('replaces a socket that may have died silently when the network comes back', async () => {
    const stop = startSessionSocket({ onEvent: vi.fn(), onOpen: vi.fn() })
    await vi.advanceTimersByTimeAsync(0)
    latest().open()
    const old = latest()

    // A Wi-Fi switch: the old socket's TCP link is gone, but no close event fired.
    window.dispatchEvent(new Event('online'))
    await vi.advanceTimersByTimeAsync(0)

    expect(old.closed).toBe(true)
    expect(FakeSocket.instances).toHaveLength(2)
    expect(latest().closed).toBe(false)
    stop()
  })

  it('reconnects at once when the browser comes back online', async () => {
    const stop = startSessionSocket({ onEvent: vi.fn(), onOpen: vi.fn() })
    await vi.advanceTimersByTimeAsync(0)
    // Several failures push the backoff out to seconds.
    for (let i = 0; i < 4; i++) {
      latest().close()
      await vi.advanceTimersByTimeAsync(30_000)
    }
    latest().close()
    const before = FakeSocket.instances.length

    window.dispatchEvent(new Event('online'))
    await vi.advanceTimersByTimeAsync(0)
    expect(FakeSocket.instances).toHaveLength(before + 1)
    stop()
  })
})
