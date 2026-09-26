import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

import {
  ApiError,
  beginSession,
  authenticate,
  logout as endSession,
  restoreSession,
  setAuthLostHandler,
  setUserChangedHandler,
} from '@/api/client'
import type { User } from '@/api/types'

type AuthState =
  | { status: 'loading' }
  // The API could not be reached at all — distinct from "not signed in", so a
  // server that is down does not look like a logout.
  | { status: 'unreachable' }
  // 'signed-out' is the user's own choice; 'expired' is a session that ended
  // under them. Only the latter should send them back where they were.
  | { status: 'anonymous'; reason: 'none' | 'signed-out' | 'expired' }
  // The token is gone but the server has not yet confirmed the cookie is
  // revoked. Shown as its own state: a login page now would invite closing
  // the tab, and a reload before the revocation lands signs straight back in.
  | { status: 'signing-out' }
  | { status: 'authenticated'; user: User }

interface AuthContextValue {
  state: AuthState
  retry: () => void
  login: (email: string, password: string) => Promise<void>
  register: (email: string, password: string, displayName: string) => Promise<void>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)


export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const [state, setState] = useState<AuthState>({ status: 'loading' })
  const [bootAttempt, setBootAttempt] = useState(0)

  // Restore the session from the refresh cookie. The access token was only
  // ever in memory, so every page load starts here.
  useEffect(() => {
    let cancelled = false
    const stale = () => cancelled
    ;(async () => {
      try {
        // StrictMode runs this twice; the single-flight refresh means both
        // runs share one POST rather than rotating the cookie twice. A sign-in
        // meanwhile needs no guard here: it waits for this refresh to land
        // (authenticate), so the boot always reports before the sign-in does.
        const user = await restoreSession()
        if (stale()) return
        setState(user ? { status: 'authenticated', user } : { status: 'anonymous', reason: 'none' })
      } catch (error) {
        if (stale()) return
        // The server said who you are not: signed out. Anything else — a
        // network failure, a 5xx, a 429 from a rate limiter — is a reason to
        // try again, not to discard a cookie that may be perfectly good.
        const refused = error instanceof ApiError && (error.status === 401 || error.status === 403)
        setState(refused ? { status: 'anonymous', reason: 'none' } : { status: 'unreachable' })
      }
    })()
    return () => {
      cancelled = true
    }
  }, [bootAttempt])

  useEffect(() => {
    setAuthLostHandler(() => setState({ status: 'anonymous', reason: 'expired' }))
    // Another tab signed in as someone else and this tab's refresh picked it
    // up. Nothing cached here belongs to that user: start over as them.
    setUserChangedHandler((user) => {
      queryClient.clear()
      setState({ status: 'authenticated', user })
    })
    return () => {
      setAuthLostHandler(null)
      setUserChangedHandler(null)
    }
  }, [queryClient])

  // Whatever the previous user left in the cache must not be shown to the
  // next one. Done after the protected tree has unmounted, so nothing
  // refetches into a cache that is being emptied.
  useEffect(() => {
    if (state.status === 'anonymous') queryClient.clear()
  }, [state.status, queryClient])

  const signIn = useCallback(
    async (path: string, body: object) => {
      const res = await authenticate(path, body)
      queryClient.clear()
      beginSession(res.access_token, res.user.id)
      setState({ status: 'authenticated', user: res.user })
    },
    [queryClient],
  )

  const value = useMemo<AuthContextValue>(
    () => ({
      state,
      retry: () => {
        setState({ status: 'loading' })
        setBootAttempt((n) => n + 1)
      },
      login: (email, password) => signIn('/api/auth/login', { email, password }),
      register: (email, password, displayName) =>
        signIn('/api/auth/register', { email, password, display_name: displayName }),
      logout: async () => {
        const request = endSession() // drops the token at once; bounded
        setState({ status: 'signing-out' })
        const { revoked } = await request
        setState({ status: 'anonymous', reason: 'signed-out' })
        if (!revoked) {
          // Signed out in this tab, but the server never confirmed: on a
          // shared machine, reloading could sign the last user back in.
          toast.warning('Signed out here, but the server couldn’t be reached', {
            description: 'This browser may still be signed in. Sign in and out again once you’re back online.',
          })
        }
      },
    }),
    [state, signIn],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside <AuthProvider>')
  return value
}

export function useCurrentUser(): User {
  const { state } = useAuth()
  if (state.status !== 'authenticated') throw new Error('useCurrentUser outside a protected route')
  return state.user
}
