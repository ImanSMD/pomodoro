import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen } from '@testing-library/react'
import { useEffect } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { beginSession, getAccessToken, setAccessToken } from '@/api/client'

import { AuthProvider, useAuth } from './AuthProvider'

let auth: ReturnType<typeof useAuth>

function Probe() {
  auth = useAuth()
  const { state } = auth
  return (
    <p>
      status: {state.status}
      {state.status === 'authenticated' && ` as ${state.user.display_name}`}
      {state.status === 'anonymous' && state.reason === 'signed-out' && ' (signed out)'}
    </p>
  )
}

function renderProvider() {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <AuthProvider>
        <Probe />
      </AuthProvider>
    </QueryClientProvider>,
  )
}

const json = (status: number, body: unknown) => Response.json(body, { status })

beforeEach(() => {
  // Client auth state is module-level and outlives each test; a page load
  // would start clean. beginSession lifts any sign-out gate a previous test left.
  beginSession('', '')
  setAccessToken(null)
})

describe('AuthProvider boot', () => {
  it('signs in from the refresh cookie', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) =>
        url.endsWith('/refresh')
          ? json(200, { access_token: 'tok', token_type: 'bearer', user: { id: 'u', display_name: 'A' } })
          : json(200, { id: 'u', email: 'a@b.c', display_name: 'A' }),
      ),
    )
    renderProvider()
    expect(await screen.findByText(/^status: authenticated/)).toBeTruthy()
  })

  it('lands on anonymous, not "unreachable", when the server refuses the refresh outright', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json(403, { detail: 'Forbidden' })))
    renderProvider()
    expect(await screen.findByText('status: anonymous')).toBeTruthy()
  })

  it('a 429 on refresh is not a sign-out', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json(429, { detail: 'Too many requests' })))
    renderProvider()
    expect(await screen.findByText('status: unreachable')).toBeTruthy()
  })

  it('reports unreachable only when the server cannot be reached', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('Failed to fetch'))))
    renderProvider()
    expect(await screen.findByText('status: unreachable')).toBeTruthy()
  })
})

describe('AuthProvider sign-out then sign-in', () => {
  it('a login made during a slow logout survives it', async () => {
    let releaseLogout!: () => void
    const gate = new Promise<void>((resolve) => (releaseLogout = resolve))
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url.endsWith('/refresh')) {
          return json(200, { access_token: 'boot', token_type: 'bearer', user: { id: 'u', display_name: 'A' } })
        }
        if (url.endsWith('/logout')) {
          await gate
          return new Response(null, { status: 204 })
        }
        return json(200, { access_token: 'second', token_type: 'bearer', user: { id: 'u' } })
      }),
    )
    renderProvider()
    await screen.findByText(/^status: authenticated/)

    let out!: Promise<void>
    let signIn!: Promise<void>
    await act(async () => {
      out = auth.logout()
      signIn = auth.login('a@b.c', 'correct-horse-1')
    })
    await act(async () => {
      releaseLogout()
      await Promise.all([out, signIn])
    })

    expect(screen.getByText(/^status: authenticated/)).toBeTruthy()
    expect(getAccessToken()).toBe('second')
  })
})

describe('AuthProvider sign-in during boot', () => {
  it('a login made while the boot refresh is still out is not undone when it lands', async () => {
    let releaseBoot!: () => void
    const gate = new Promise<void>((resolve) => (releaseBoot = resolve))
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url.endsWith('/refresh')) {
          await gate // slow, then says: no cookie
          return json(401, { detail: 'Invalid refresh token' })
        }
        return json(200, { access_token: 'mine', token_type: 'bearer', user: { id: 'u' } })
      }),
    )
    renderProvider()
    expect(screen.getByText('status: loading')).toBeTruthy()

    let signIn!: Promise<void>
    await act(async () => {
      signIn = auth.login('a@b.c', 'correct-horse-1')
    })
    await act(async () => {
      releaseBoot()
      await signIn
      // Let the boot refresh finish for good — including the one delayed
      // retry it makes on a 401 where Web Locks are missing, as in jsdom.
      await new Promise((resolve) => setTimeout(resolve, 1_500))
    })

    expect(screen.getByText(/^status: authenticated/)).toBeTruthy()
    expect(getAccessToken()).toBe('mine')
  })
})

describe('AuthProvider sign-in over a restoring session', () => {
  it('a boot that restores another account late does not replace the new sign-in', async () => {
    let releaseBoot!: () => void
    const bootGate = new Promise<void>((resolve) => (releaseBoot = resolve))
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url.endsWith('/refresh')) {
          await bootGate // the previous account's cookie, answered late
          return json(200, { access_token: 'x', token_type: 'bearer', user: { id: 'x', display_name: 'Previous' } })
        }
        return json(200, { access_token: 'y', token_type: 'bearer', user: { id: 'y', display_name: 'New' } })
      }),
    )
    renderProvider()

    let signIn!: Promise<void>
    await act(async () => {
      signIn = auth.login('y@b.c', 'correct-horse-1')
    })
    await act(async () => {
      releaseBoot()
      await signIn
    })

    expect(screen.getByText('status: authenticated as New')).toBeTruthy()
  })
})

describe('AuthProvider failed sign-in during boot', () => {
  it('a wrong password does not throw away the session the boot restores', async () => {
    let releaseBoot!: () => void
    const bootGate = new Promise<void>((resolve) => (releaseBoot = resolve))
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url.endsWith('/refresh')) {
          await bootGate
          return json(200, { access_token: 'x', token_type: 'bearer', user: { id: 'x', display_name: 'Restored' } })
        }
        return json(401, { detail: 'Incorrect email or password' })
      }),
    )
    renderProvider()

    let attempt!: Promise<unknown>
    await act(async () => {
      attempt = auth.login('x@b.c', 'wrong-password').catch(() => undefined)
    })
    await act(async () => {
      releaseBoot()
      await attempt
    })

    expect(screen.getByText('status: authenticated as Restored')).toBeTruthy()
  })
})

describe('AuthProvider sign-out', () => {
  it('says "signing out" until the server confirms — the login page must not imply a revoked cookie', async () => {
    let releaseLogout!: () => void
    const gate = new Promise<void>((resolve) => (releaseLogout = resolve))
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url.endsWith('/refresh')) {
          return json(200, { access_token: 'x', token_type: 'bearer', user: { id: 'x', display_name: 'X' } })
        }
        await gate
        return new Response(null, { status: 204 })
      }),
    )
    renderProvider()
    await screen.findByText(/^status: authenticated/)

    let out!: Promise<void>
    await act(async () => {
      out = auth.logout()
    })
    expect(screen.getByText('status: signing-out')).toBeTruthy()

    await act(async () => {
      releaseLogout()
      await out
    })
    expect(screen.getByText('status: anonymous (signed out)')).toBeTruthy()
  })

  it('gives up waiting on a stuck logout after a bound, and signs the tab out anyway', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url.endsWith('/refresh')) {
          return json(200, { access_token: 'x', token_type: 'bearer', user: { id: 'x', display_name: 'X' } })
        }
        // Never answers — but, like real fetch, gives up when aborted.
        return new Promise<Response>((_resolve, reject) =>
          init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError'))),
        )
      }),
    )
    renderProvider()
    await screen.findByText(/^status: authenticated/)

    await act(async () => {
      void auth.logout()
    })
    expect(screen.getByText('status: signing-out')).toBeTruthy()
    await act(() => vi.advanceTimersByTimeAsync(20_000))
    expect(screen.getByText('status: anonymous (signed out)')).toBeTruthy()
  })
})

describe('AuthProvider account switch', () => {
  it('remounts the signed-in tree, so nothing keeps showing the previous account', async () => {
    const { MemoryRouter, Route, Routes } = await import('react-router-dom')
    const { ProtectedRoute } = await import('./ProtectedRoute')
    let mounts = 0
    function Inside() {
      useEffectOnce(() => {
        mounts += 1
      })
      auth = useAuth()
      return <p>inside</p>
    }
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) =>
        url.endsWith('/refresh')
          ? json(200, { access_token: 'x', token_type: 'bearer', user: { id: 'user-x', display_name: 'X' } })
          : json(200, { id: 'user-x', email: 'x@b.c', display_name: 'X' }),
      ),
    )
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
          <AuthProvider>
            <Routes>
              <Route element={<ProtectedRoute />}>
                <Route path="/" element={<Inside />} />
              </Route>
            </Routes>
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    )
    await screen.findByText('inside')
    expect(mounts).toBe(1)
    // Another tab signs in as someone else; this tab's next refresh says so.
    const client = await import('@/api/client')
    client.beginSession('x', 'user-x')
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => json(200, { access_token: 'y', token_type: 'bearer', user: { id: 'user-y', display_name: 'Y' } })),
    )
    await act(async () => {
      await client.refreshAccessToken()
    })
    expect(mounts).toBe(2)
  })
})

function useEffectOnce(fn: () => void) {
  useEffect(fn, [])
}

