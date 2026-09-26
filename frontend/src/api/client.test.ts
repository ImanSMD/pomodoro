import { beforeEach, describe, expect, it, vi } from 'vitest'

type Client = typeof import('./client')

// The token store is module state, so each test gets a fresh module.
let client: Client
let fetchMock: ReturnType<typeof vi.fn>

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function calls(path: string) {
  return fetchMock.mock.calls.filter(([url]) => String(url).endsWith(path))
}

beforeEach(async () => {
  vi.resetModules()
  fetchMock = vi.fn()
  vi.stubGlobal('fetch', fetchMock)
  client = await import('./client')
})

describe('api()', () => {
  it('attaches the in-memory access token as a bearer header', async () => {
    client.setAccessToken('tok-1')
    fetchMock.mockResolvedValue(json(200, { ok: true }))

    await client.api('/api/auth/me')

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('http://api.test/api/auth/me')
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer tok-1')
  })

  it('ten parallel 401s trigger exactly one refresh, then every request retries', async () => {
    client.setAccessToken('stale')
    let releaseRefresh!: () => void
    const refreshGate = new Promise<void>((resolve) => (releaseRefresh = resolve))

    fetchMock.mockImplementation(async (url: string, init: RequestInit) => {
      if (url.endsWith('/api/auth/refresh')) {
        await refreshGate
        return json(200, { access_token: 'fresh', token_type: 'bearer', user: {} })
      }
      const auth = (init.headers as Record<string, string>).Authorization
      return auth === 'Bearer fresh' ? json(200, { ok: true }) : json(401, { detail: 'expired' })
    })

    const requests = Array.from({ length: 10 }, () => client.api('/api/tasks'))
    // Let every first attempt come back 401 before the refresh resolves.
    await vi.waitFor(() => expect(calls('/api/tasks')).toHaveLength(10))
    releaseRefresh()

    const results = await Promise.all(requests)
    expect(results).toHaveLength(10)
    expect(calls('/api/auth/refresh')).toHaveLength(1)
    expect(calls('/api/tasks')).toHaveLength(20)
    expect(client.getAccessToken()).toBe('fresh')
  })

  it('a 401 arriving after another request already refreshed reuses that token', async () => {
    client.setAccessToken('stale')
    fetchMock.mockImplementation(async (url: string, init: RequestInit) => {
      if (url.endsWith('/api/auth/refresh')) {
        return json(200, { access_token: 'fresh', token_type: 'bearer', user: {} })
      }
      const auth = (init.headers as Record<string, string>).Authorization
      return auth === 'Bearer fresh' ? json(200, {}) : json(401, {})
    })

    await client.api('/api/tasks')
    // This one was "sent" with the stale token before the refresh landed.
    client.setAccessToken('stale')
    fetchMock.mockClear()
    const slow = client.api('/api/categories')
    client.setAccessToken('fresh')
    await slow
    expect(calls('/api/auth/refresh')).toHaveLength(0)
  })

  it('a rejected refresh clears the token, reports auth lost, and throws 401', async () => {
    vi.useFakeTimers()
    client.setAccessToken('stale')
    const lost = vi.fn()
    client.setAuthLostHandler(lost)
    fetchMock.mockResolvedValue(json(401, { detail: 'nope' }))

    const request = client.api('/api/tasks')
    const settled = expect(request).rejects.toMatchObject({ status: 401 })
    await vi.advanceTimersByTimeAsync(1_000)
    await settled
    vi.useRealTimers()
    expect(lost).toHaveBeenCalledTimes(1)
    expect(client.getAccessToken()).toBeNull()
  })

  it('a network failure during refresh does not log the user out', async () => {
    client.setAccessToken('stale')
    const lost = vi.fn()
    client.setAuthLostHandler(lost)
    fetchMock.mockImplementation(async (url: string) => {
      if (url.endsWith('/api/auth/refresh')) throw new TypeError('Failed to fetch')
      return json(401, {})
    })

    await expect(client.api('/api/tasks')).rejects.toThrow('Failed to fetch')
    expect(lost).not.toHaveBeenCalled()
  })

  it('login failures are not retried through a refresh', async () => {
    fetchMock.mockResolvedValue(json(401, { detail: 'Incorrect email or password' }))

    await expect(
      client.api('/api/auth/login', { method: 'POST', body: { email: 'a@b.c', password: 'x' } }),
    ).rejects.toThrow('Incorrect email or password')
    expect(calls('/api/auth/refresh')).toHaveLength(0)
  })

  it('surfaces FastAPI validation messages', async () => {
    fetchMock.mockResolvedValue(
      json(422, { detail: [{ loc: ['body', 'name'], msg: 'Value error, name cannot be blank' }] }),
    )
    await expect(client.api('/api/categories', { method: 'POST', body: {} })).rejects.toThrow(
      'name cannot be blank',
    )
  })

  it('never writes the token to web storage', async () => {
    fetchMock.mockResolvedValue(json(200, { access_token: 'fresh', token_type: 'bearer', user: {} }))
    await client.refreshAccessToken()

    expect(client.getAccessToken()).toBe('fresh')
    expect(window.localStorage.length).toBe(0)
    expect(window.sessionStorage.length).toBe(0)
  })
})

describe('the auth epoch', () => {
  it('a refresh refused because the user signed out is not reported as an expired session', async () => {
    const lost = vi.fn()
    client.setAuthLostHandler(lost)
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }))
    await client.logout()

    expect(await client.refreshAccessToken()).toBeNull()
    expect(lost).not.toHaveBeenCalled()
  })

  it('logout is bounded even behind a refresh that never answers', async () => {
    vi.useFakeTimers()
    fetchMock.mockImplementation(async (url: string, init: RequestInit) =>
      url.endsWith('/api/auth/refresh')
        ? new Promise<Response>(() => {}) // hangs; refresh has no deadline by design
        : new Promise<Response>((_resolve, reject) => {
            // Like fetch: an already-aborted signal rejects at once.
            if (init.signal?.aborted) reject(new DOMException('aborted', 'AbortError'))
            init.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
          }),
    )
    void client.refreshAccessToken()
    const out = client.logout()
    await vi.advanceTimersByTimeAsync(20_000)
    expect(await out).toEqual({ revoked: false })
  })

  it('a refresh that lands after a sign-out and a new sign-in is discarded', async () => {
    let answer!: (r: Response) => void
    fetchMock.mockImplementation(async (url: string) =>
      url.endsWith('/api/auth/refresh')
        ? new Promise<Response>((resolve) => (answer = resolve))
        : new Response(null, { status: 204 }),
    )
    client.beginSession('old', 'user-x')
    const late = client.refreshAccessToken()
    client.beginSession('new', 'user-y') // signed in afresh meanwhile
    answer(json(200, { access_token: 'from-the-old-cookie', token_type: 'bearer', user: { id: 'user-x' } }))

    expect(await late).toBeNull()
    expect(client.getAccessToken()).toBe('new')
  })
})

describe('sign-in', () => {
  it('waits for a refresh in flight, so the rotated cookie of the old account cannot land over the new one', async () => {
    let answerRefresh!: () => void
    const refreshGate = new Promise<void>((resolve) => (answerRefresh = resolve))
    const order: string[] = []
    fetchMock.mockImplementation(async (url: string) => {
      if (url.endsWith('/api/auth/refresh')) {
        await refreshGate
        order.push('refresh')
        return json(200, { access_token: 'old', token_type: 'bearer', user: { id: 'x' } })
      }
      order.push('login')
      return json(200, { access_token: 'new', token_type: 'bearer', user: { id: 'y' } })
    })

    const refreshing = client.refreshAccessToken()
    const signingIn = client.authenticate('/api/auth/login', { email: 'y@b.c', password: 'p' })
    await Promise.resolve()
    answerRefresh()
    await Promise.all([refreshing, signingIn])
    expect(order).toEqual(['refresh', 'login'])
  })
})

describe('sign-in across tabs', () => {
  it('waits for the cross-tab lock, where another tab may be mid-refresh with the old account', async () => {
    // A minimal Web Locks: one holder at a time, the rest queue.
    let tail = Promise.resolve()
    const request = (_name: string, ...rest: unknown[]) => {
      const fn = rest.at(-1) as () => Promise<unknown>
      const run = tail.then(fn)
      tail = run.then(() => undefined, () => undefined)
      return run
    }
    vi.stubGlobal('navigator', { ...navigator, locks: { request } })

    let releaseOtherTab!: () => void
    const otherTab = request('pomodoro-auth-refresh', () => new Promise<void>((resolve) => (releaseOtherTab = resolve)))
    fetchMock.mockResolvedValue(json(200, { access_token: 'new', token_type: 'bearer', user: { id: 'y' } }))

    const signingIn = client.authenticate('/api/auth/login', { email: 'y@b.c', password: 'p' })
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(calls('/api/auth/login')).toHaveLength(0) // still waiting on the other tab

    releaseOtherTab()
    await Promise.all([otherTab, signingIn])
    expect(calls('/api/auth/login')).toHaveLength(1)
  })
})

describe('sign-in deadline', () => {
  it('a login that never answers is abandoned, not left holding the cross-tab lock', async () => {
    vi.useFakeTimers()
    fetchMock.mockImplementation(
      (_url: string, init: RequestInit) =>
        new Promise((_resolve, reject) =>
          init.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError'))),
        ),
    )
    const signingIn = client.authenticate('/api/auth/login', { email: 'y@b.c', password: 'p' })
    const settled = expect(signingIn).rejects.toBeInstanceOf(client.NetworkError)
    await vi.advanceTimersByTimeAsync(60_000)
    await settled
  })
})

describe('logout() and sign-in', () => {
  it('a sign-in during a slow logout waits for it, so the logout cannot undo it', async () => {
    let releaseLogout!: () => void
    const gate = new Promise<void>((resolve) => (releaseLogout = resolve))
    const order: string[] = []
    fetchMock.mockImplementation(async (url: string) => {
      if (url.endsWith('/api/auth/logout')) {
        await gate
        order.push('logout')
        return new Response(null, { status: 204 })
      }
      order.push('login')
      return json(200, { access_token: 'new', token_type: 'bearer', user: {} })
    })

    client.setAccessToken('old')
    const out = client.logout()
    const signIn = (async () => {
      const res = await client.authenticate('/api/auth/login', {})
      client.beginSession(res.access_token, 'u')
    })()
    await Promise.resolve()
    releaseLogout()
    await Promise.all([out, signIn])

    expect(order).toEqual(['logout', 'login'])
    expect(client.getAccessToken()).toBe('new')
  })

  it('is single-flight: a double click sends one logout', async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }))
    await Promise.all([client.logout(), client.logout()])
    expect(calls('/api/auth/logout')).toHaveLength(1)
  })
})

describe('logout()', () => {
  it('waits for an in-flight refresh, then revokes — the refresh cannot sign the tab back in', async () => {
    let releaseRefresh!: () => void
    const gate = new Promise<void>((resolve) => (releaseRefresh = resolve))
    const order: string[] = []
    fetchMock.mockImplementation(async (url: string) => {
      if (url.endsWith('/api/auth/refresh')) {
        await gate
        order.push('refresh')
        return json(200, { access_token: 'late', token_type: 'bearer', user: {} })
      }
      order.push('logout')
      return new Response(null, { status: 204 })
    })

    const refreshing = client.refreshAccessToken()
    const loggingOut = client.logout()
    await Promise.resolve()
    releaseRefresh()
    await Promise.all([refreshing, loggingOut])

    // The logout POST carries the cookie that refresh just installed.
    expect(order).toEqual(['refresh', 'logout'])
    expect(client.getAccessToken()).toBeNull()

    // And that refresh landing did not re-arm refreshing for this tab.
    expect(await client.refreshAccessToken()).toBeNull()
    expect(order).toEqual(['refresh', 'logout'])
  })

  it('a refresh queued behind a failed logout cannot sign the tab back in', async () => {
    client.setAccessToken('tok')
    fetchMock.mockImplementation(async (url: string) => {
      if (url.endsWith('/api/auth/logout')) throw new TypeError('Failed to fetch')
      return json(200, { access_token: 'revived', token_type: 'bearer', user: {} })
    })
    await client.logout()

    // A request that 401s after the logout tries to refresh.
    expect(await client.refreshAccessToken()).toBeNull()
    expect(client.getAccessToken()).toBeNull()
    expect(calls('/api/auth/refresh')).toHaveLength(0)

    // Signing in again lifts it.
    client.beginSession('new-login', 'u')
    expect(await client.refreshAccessToken()).toBe('revived')
  })

  it('still clears the token when the logout request fails, and says the cookie was not revoked', async () => {
    client.setAccessToken('tok')
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))
    expect(await client.logout()).toEqual({ revoked: false })
    expect(client.getAccessToken()).toBeNull()
  })

  it('a logout the server answers with an error is not a revocation', async () => {
    fetchMock.mockResolvedValue(json(500, { detail: 'boom' }))
    expect(await client.logout()).toEqual({ revoked: false })
  })

  it('reports a revoked cookie when the logout request lands', async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }))
    expect(await client.logout()).toEqual({ revoked: true })
  })
})

describe('refreshAccessToken()', () => {
  it('with Web Locks, takes a 401 as final at once — no tab of ours can be a concurrent duplicate', async () => {
    const request = vi.fn((_name: string, ...rest: unknown[]) => (rest.at(-1) as () => Promise<unknown>)())
    vi.stubGlobal('navigator', { ...navigator, locks: { request } })
    fetchMock.mockResolvedValue(json(401, { detail: 'Invalid refresh token' }))

    expect(await client.refreshAccessToken()).toBeNull()
    expect(calls('/api/auth/refresh')).toHaveLength(1)
  })

  it('without Web Locks, retries once after a 401: the server refuses a duplicate inside its grace window while a sibling cookie lands', async () => {
    vi.stubGlobal('navigator', { ...navigator, locks: undefined })
    vi.useFakeTimers()
    const lost = vi.fn()
    client.setAuthLostHandler(lost)
    fetchMock
      .mockResolvedValueOnce(json(401, { detail: 'Invalid refresh token' }))
      .mockResolvedValueOnce(json(200, { access_token: 'fresh', token_type: 'bearer', user: {} }))

    const result = client.refreshAccessToken()
    await vi.advanceTimersByTimeAsync(1_000)
    expect(await result).toBe('fresh')
    expect(lost).not.toHaveBeenCalled()
    vi.useRealTimers()
  })

  it('gives up after the retry is refused too', async () => {
    vi.stubGlobal('navigator', { ...navigator, locks: undefined })
    vi.useFakeTimers()
    const lost = vi.fn()
    client.setAuthLostHandler(lost)
    fetchMock.mockResolvedValue(json(401, { detail: 'Invalid refresh token' }))

    const result = client.refreshAccessToken()
    await vi.advanceTimersByTimeAsync(1_000)
    expect(await result).toBeNull()
    expect(calls('/api/auth/refresh')).toHaveLength(2)
    expect(lost).toHaveBeenCalledTimes(1)
    vi.useRealTimers()
  })

  it('serialises through the cross-tab lock when Web Locks exist', async () => {
    const request = vi.fn((_name: string, ...rest: unknown[]) => (rest.at(-1) as () => Promise<unknown>)())
    vi.stubGlobal('navigator', { ...navigator, locks: { request } })
    fetchMock.mockResolvedValue(json(200, { access_token: 'fresh', token_type: 'bearer', user: {} }))

    await client.refreshAccessToken()

    expect(request).toHaveBeenCalledWith('pomodoro-auth-refresh', {}, expect.any(Function))
  })
})

describe('tokenExpiresAt()', () => {
  it('reads exp from a base64url payload', () => {
    const payload = btoa(JSON.stringify({ sub: 'u', exp: 1_900_000_000 }))
      .replace(/\+/g, '-')
      .replace(/\//g, '_')
      .replace(/=+$/, '')
    expect(client.tokenExpiresAt(`h.${payload}.s`)).toBe(1_900_000_000)
    expect(client.tokenExpiresAt('garbage')).toBeNull()
  })
})

describe('a refresh that comes back as someone else', () => {
  it('reports the account change instead of quietly carrying on as the new user', async () => {
    // Tabs share one refresh cookie: another tab signed in as someone else.
    const changed = vi.fn()
    client.setUserChangedHandler(changed)
    client.beginSession('tok-x', 'user-x')
    fetchMock.mockResolvedValue(json(200, { access_token: 'tok-y', token_type: 'bearer', user: { id: 'user-y' } }))

    await client.refreshAccessToken()
    expect(changed).toHaveBeenCalledWith(expect.objectContaining({ id: 'user-y' }))
  })

  it('does not replay a request under the other account', async () => {
    client.setUserChangedHandler(vi.fn())
    client.beginSession('tok-x', 'user-x')
    fetchMock.mockImplementation(async (url: string) =>
      url.endsWith('/api/auth/refresh')
        ? json(200, { access_token: 'tok-y', token_type: 'bearer', user: { id: 'user-y' } })
        : json(401, { detail: 'expired' }),
    )

    await expect(client.api('/api/tasks', { method: 'POST', body: { title: 'mine' } })).rejects.toMatchObject({
      status: 401,
    })
    // Sent once, as X, and never re-sent as Y.
    expect(calls('/api/tasks')).toHaveLength(1)
  })

  it('a request that 401s after another request already switched accounts is not replayed either', async () => {
    client.setUserChangedHandler(vi.fn())
    client.beginSession('tok-x', 'user-x')
    let releaseSecond!: () => void
    const second = new Promise<void>((resolve) => (releaseSecond = resolve))
    fetchMock.mockImplementation(async (url: string, init: RequestInit) => {
      if (url.endsWith('/api/auth/refresh')) {
        return json(200, { access_token: 'tok-y', token_type: 'bearer', user: { id: 'user-y' } })
      }
      const auth = (init.headers as Record<string, string>).Authorization
      if (url.endsWith('/api/categories') && auth === 'Bearer tok-x') await second // its 401 comes late
      return json(auth === 'Bearer tok-y' ? 200 : 401, {})
    })

    const first = client.api('/api/tasks').catch(() => undefined) // switches the tab to Y
    const late = client.api('/api/categories', { method: 'POST', body: { name: 'mine' } })
    await first
    releaseSecond()

    await expect(late).rejects.toMatchObject({ status: 401 })
    const replayedAsY = fetchMock.mock.calls.filter(
      ([url, init]) =>
        String(url).endsWith('/api/categories') &&
        (init.headers as Record<string, string>).Authorization === 'Bearer tok-y',
    )
    expect(replayedAsY).toHaveLength(0)
  })

  it('stays quiet when the refresh is for the same account', async () => {
    const changed = vi.fn()
    client.setUserChangedHandler(changed)
    client.beginSession('tok-x', 'user-x')
    fetchMock.mockResolvedValue(json(200, { access_token: 'tok-x2', token_type: 'bearer', user: { id: 'user-x' } }))

    await client.refreshAccessToken()
    expect(changed).not.toHaveBeenCalled()
  })
})

describe('failures', () => {
  it('a slow refresh is given a full minute: abandoning one the server completes loses the rotated cookie', async () => {
    vi.useFakeTimers()
    let answer!: (r: Response) => void
    // Behaves like fetch: rejects if its signal aborts, else waits for the answer.
    fetchMock.mockImplementation(
      (_url: string, init: RequestInit) =>
        new Promise<Response>((resolve, reject) => {
          answer = resolve
          init.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
        }),
    )
    const result = client.refreshAccessToken()
    await vi.advanceTimersByTimeAsync(59_000)
    answer(json(200, { access_token: 'late-but-valid', token_type: 'bearer', user: {} }))
    expect(await result).toBe('late-but-valid')
  })

  it('a refresh that never answers is abandoned after a minute, rather than hold every tab on the lock', async () => {
    vi.useFakeTimers()
    fetchMock.mockImplementation(
      (_url: string, init: RequestInit) =>
        new Promise((_resolve, reject) =>
          init.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError'))),
        ),
    )
    const result = client.refreshAccessToken()
    const settled = expect(result).rejects.toBeInstanceOf(client.NetworkError)
    await vi.advanceTimersByTimeAsync(60_000)
    await settled
  })

  it('a logout that hangs is abandoned, so the cross-tab lock is not held forever', async () => {
    vi.useFakeTimers()
    fetchMock.mockImplementation(
      (_url: string, init: RequestInit) =>
        new Promise((_resolve, reject) =>
          init.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError'))),
        ),
    )
    const result = client.logout()
    await vi.advanceTimersByTimeAsync(20_000)
    expect(await result).toEqual({ revoked: false })
  })

  it('a caller signal that is already aborted fails at once, even with a timeout', async () => {
    fetchMock.mockImplementation(
      (_url: string, init: RequestInit) =>
        init.signal?.aborted
          ? Promise.reject(new DOMException('aborted', 'AbortError'))
          : new Promise(() => {}),
    )
    const caller = new AbortController()
    caller.abort()
    const error = await client.send('http://api.test/x', { signal: caller.signal }, 15_000).catch((e: unknown) => e)
    expect((error as Error).name).toBe('AbortError')
  })

  it('a network failure is a NetworkError; anything else is not described as one', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))
    const error = await client.api('/api/tasks').catch((e: unknown) => e)
    expect(error).toBeInstanceOf(client.NetworkError)
    expect(client.describeError(error)).toMatch(/reach the server/)
    expect(client.describeError(new SyntaxError('Unexpected token <'))).not.toMatch(/reach the server/)
  })

  it('a request with a timeout still honours its caller aborting it', async () => {
    fetchMock.mockImplementation(
      (_url: string, init: RequestInit) =>
        new Promise((_resolve, reject) =>
          init.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError'))),
        ),
    )
    const caller = new AbortController()
    const request = client.send('http://api.test/x', { signal: caller.signal }, 15_000).catch((e: unknown) => e)
    caller.abort()
    const error = await request
    expect((error as Error).name).toBe('AbortError')
  })

  it('a caller aborting its own request is not turned into a network failure', async () => {
    fetchMock.mockRejectedValue(new DOMException('aborted', 'AbortError'))
    const error = await client.api('/api/tasks').catch((e: unknown) => e)
    expect((error as Error).name).toBe('AbortError')
  })

  it('an error body with no usable detail falls back to the status text', async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ detail: [{ loc: ['body'] }] }), { status: 422, statusText: 'Unprocessable Entity' }),
    )
    await expect(client.api('/api/tasks', { method: 'POST', body: {} })).rejects.toThrow('Unprocessable Entity')
  })
})

describe('wsUrl()', () => {
  it('uses the same base as apiUrl(), swapping only the scheme', () => {
    expect(client.wsUrl('/ws?token=t')).toBe('ws://api.test/ws?token=t')
  })

  it('keeps a path prefix in VITE_API_URL, as HTTP calls do', async () => {
    vi.stubEnv('VITE_API_URL', 'https://host.test/pomo/')
    vi.resetModules()
    const prefixed = await import('./client')
    expect(prefixed.apiUrl('/api/tasks')).toBe('https://host.test/pomo/api/tasks')
    expect(prefixed.wsUrl('/ws')).toBe('wss://host.test/pomo/ws')
    vi.unstubAllEnvs()
  })
})
