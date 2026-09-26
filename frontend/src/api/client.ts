import type { AuthResponse, User } from './types'

// Derived from API_PORT by compose — never hardcoded. Empty means same origin.
const API_URL = (import.meta.env.VITE_API_URL ?? '').replace(/\/+$/, '')

export function apiUrl(path: string): string {
  return `${API_URL}${path}`
}

/** apiUrl(path) on the WebSocket scheme (http→ws, https→wss). Built from
 *  apiUrl so a path prefix in VITE_API_URL applies to both alike. */
export function wsUrl(path: string): string {
  const url = new URL(apiUrl(path), window.location.origin)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  return url.toString()
}

/** The request never got an answer: offline, DNS, refused, timed out. */
export class NetworkError extends Error {
  constructor(message = 'Network request failed') {
    super(message)
    this.name = 'NetworkError'
  }
}

// Everything a logout waits on shares this deadline. Abandoning one is safe:
// the server revokes if the request reached it.
const LOGOUT_TIMEOUT_MS = 20_000
// A refresh is not safe to abandon: if the server completes it anyway, the
// rotated cookie never reaches the browser, which is left holding a revoked
// one. But one that never answers holds the cross-tab lock, and every tab's
// boot waits behind it. A minute is far past any real refresh, and turns a
// hung server into "can't reach the server — try again" instead of a spinner.
const REFRESH_TIMEOUT_MS = 60_000

/**
 * fetch, with its failures sorted: no answer at all becomes NetworkError,
 * and a timeout (when asked for) is one of those. A caller aborting its own
 * request — TanStack cancelling a query — stays an AbortError.
 */
export async function send(url: string, init: RequestInit, timeoutMs?: number): Promise<Response> {
  if (timeoutMs === undefined) return classify(() => fetch(url, init), () => false)

  // One controller aborted by either the deadline or the caller, so a timeout
  // never costs the caller its own ability to cancel.
  const controller = new AbortController()
  let timedOut = false
  const timer = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeoutMs)
  const onCallerAbort = () => controller.abort()
  if (init.signal?.aborted) controller.abort()
  init.signal?.addEventListener('abort', onCallerAbort)
  try {
    return await classify(() => fetch(url, { ...init, signal: controller.signal }), () => timedOut)
  } finally {
    clearTimeout(timer)
    init.signal?.removeEventListener('abort', onCallerAbort)
  }
}

async function classify(request: () => Promise<Response>, timedOut: () => boolean): Promise<Response> {
  try {
    return await request()
  } catch (error) {
    if (timedOut()) throw new NetworkError('The server took too long to answer')
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new NetworkError(error instanceof Error ? error.message : undefined)
  }
}

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** Words for a person. An ApiError already carries the server's detail; a
 *  bare TypeError is the browser's "Failed to fetch", which says nothing. */
export function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.message
  if (error instanceof NetworkError) return 'Can’t reach the server. Check your connection and try again.'
  return 'Something went wrong. Try again, or reload the page.'
}

// ---------------------------------------------------------------------------
// The access token lives here, in module memory, and nowhere else. Never
// localStorage or sessionStorage: anything there is readable by any XSS on the
// page. A reload loses it on purpose — the httpOnly refresh cookie gets a new
// one.

let accessToken: string | null = null
// Whose session this tab is showing. Every tab shares one refresh cookie, so
// if another tab signs in as someone else, this tab's next refresh returns
// that other user — which must not pass unnoticed.
let sessionUserId: string | null = null
let onUserChanged: ((user: User) => void) | null = null

/** Called when a refresh comes back for a different account than the one
 *  this tab is showing. AuthProvider switches over cleanly. */
export function setUserChangedHandler(handler: ((user: User) => void) | null): void {
  onUserChanged = handler
}

// Set by logout(), lifted only by beginSession(). While set, refresh is refused
// outright: a refresh queued behind a logout whose POST failed would still
// carry a valid cookie, and would put a token back in this tab's memory.
let signedOut = false

export function getAccessToken(): string | null {
  return accessToken
}

export function setAccessToken(token: string | null): void {
  accessToken = token
}

/** An explicit sign-in (login or register). The only thing that lifts the
 *  logout gate — a refresh landing must not, or one in flight during logout
 *  would re-arm refreshing for a tab the user has just signed out of. */
export function beginSession(token: string, userId: string): void {
  authEpoch += 1
  signedOut = false
  sessionUserId = userId
  setAccessToken(token)
}

/** Seconds-since-epoch expiry from the JWT payload, or null if unreadable.
 *  Read only to decide when to refresh — the server does the verifying. */
export function tokenExpiresAt(token: string): number | null {
  try {
    const payload = token.split('.')[1]
    const json = atob(payload.replace(/-/g, '+').replace(/_/g, '/'))
    const exp = (JSON.parse(json) as { exp?: unknown }).exp
    return typeof exp === 'number' ? exp : null
  } catch {
    return null
  }
}

let onAuthLost: (() => void) | null = null

/** Called once the refresh cookie is known to be dead. AuthProvider uses it
 *  to drop the user, which sends ProtectedRoute back to /login. */
export function setAuthLostHandler(handler: (() => void) | null): void {
  onAuthLost = handler
}

// ---------------------------------------------------------------------------
// Refresh

async function readDetail(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown }
    if (typeof body.detail === 'string') return body.detail
    // FastAPI validation errors: [{loc, msg, ...}, ...]
    if (Array.isArray(body.detail)) {
      const messages = body.detail
        .map((d: { msg?: string }) => d.msg?.replace(/^Value error, /, ''))
        .filter(Boolean)
      if (messages.length) return messages.join('. ')
    }
  } catch {
    // Not JSON — fall through to the status text.
  }
  return res.statusText || `Request failed (${res.status})`
}

// A 401 can be benign: inside REFRESH_REUSE_GRACE the server refuses a
// duplicate of a cookie a sibling request has just rotated, and leaves that
// sibling's successor cookie alone. That needs two of our refreshes in flight
// at once, which the cross-tab lock rules out — so only without Web Locks is
// a 401 worth one retry once the sibling's cookie has landed. With them, a
// 401 is final at once, and a signed-out page load pays no delay.
const REFRESH_RETRY_DELAY_MS = 1_000

function hasWebLocks(): boolean {
  return typeof navigator !== 'undefined' && !!navigator.locks
}

async function postRefreshOnce(): Promise<Response> {
  return send(apiUrl('/api/auth/refresh'), { method: 'POST', credentials: 'include' }, REFRESH_TIMEOUT_MS)
}

/** null means the cookie is definitively rejected; a network or 5xx failure
 *  throws instead, so a blip does not log the user out. */
async function postRefresh(): Promise<AuthResponse | null> {
  let res = await postRefreshOnce()
  if (res.status === 401 && !hasWebLocks()) {
    await new Promise((resolve) => setTimeout(resolve, REFRESH_RETRY_DELAY_MS))
    res = await postRefreshOnce()
  }
  if (res.status === 401) return null
  if (!res.ok) throw new ApiError(res.status, await readDetail(res))
  return (await res.json()) as AuthResponse
}

/** Serialise refreshes across tabs. The refresh token rotates on every use
 *  and all tabs share one cookie jar, so two tabs refreshing at once would
 *  both send the same cookie; the loser lands in the server's grace window
 *  and gets a 401. Under the lock, the second tab waits and then sends the
 *  cookie the first one just received. A signal lets a caller stop waiting. */
async function withRefreshLock<T>(fn: () => Promise<T>, signal?: AbortSignal): Promise<T> {
  if (hasWebLocks()) {
    return await navigator.locks.request('pomodoro-auth-refresh', signal ? { signal } : {}, fn)
  }
  return fn()
}

interface RefreshResult {
  token: string | null
  user: User | null
  /** The refresh answered for a different account than this tab's. */
  userChanged: boolean
}

let refreshInFlight: Promise<RefreshResult> | null = null
// Bumped by every sign-in and sign-out. A refresh that lands under an older
// epoch answers for a session this tab no longer has, so it is discarded
// whole: it can neither install its token over a new sign-in, nor report a
// lapsed session while the user is signing out on purpose.
let authEpoch = 0

function refreshDetailed(): Promise<RefreshResult> {
  if (!refreshInFlight) {
    const epoch = authEpoch
    refreshInFlight = (async () => {
      try {
        // Checked under the lock too, so a refresh queued behind a logout
        // never sends the cookie.
        const res = await withRefreshLock(() => (signedOut ? Promise.resolve(null) : postRefresh()))
        if (epoch !== authEpoch || signedOut) return { token: null, user: null, userChanged: false }

        const token = res?.access_token ?? null
        setAccessToken(token)
        if (!res || token === null) {
          onAuthLost?.()
          return { token: null, user: null, userChanged: false }
        }
        // The first refresh (the boot restore) establishes whose tab this is.
        if (sessionUserId === null) sessionUserId = res.user.id
        if (res.user.id !== sessionUserId) {
          // A different account is a different session: bump the epoch, so
          // every request sent under the old one is refused, not replayed.
          authEpoch += 1
          sessionUserId = res.user.id
          onUserChanged?.(res.user)
          return { token, user: res.user, userChanged: true }
        }
        return { token, user: res.user, userChanged: false }
      } finally {
        refreshInFlight = null
      }
    })()
  }
  return refreshInFlight
}

/** Restore the session from the refresh cookie on page load. The refresh
 *  already says who the user is, so no /auth/me round trip is needed. */
export async function restoreSession(): Promise<User | null> {
  return (await refreshDetailed()).user
}

/** Single-flight within the tab: every caller during one refresh shares its
 *  promise, so ten parallel 401s cost one POST /auth/refresh. */
export async function refreshAccessToken(): Promise<string | null> {
  return (await refreshDetailed()).token
}

export interface LogoutResult {
  /** false: the server never confirmed, so the refresh cookie may still be
   *  live and a reload could sign this browser back in. */
  revoked: boolean
}

let logoutInFlight: Promise<LogoutResult> | null = null

/**
 * End the session. The token goes at once. A refresh already in flight is
 * allowed to land first where it can — its Set-Cookie is the cookie to revoke,
 * and the epoch discards its token — and the POST runs under the cross-tab
 * lock, so a refresh begun meanwhile queues behind it and meets a cleared
 * cookie. Everything it waits on shares one deadline: a stuck refresh (which
 * has no deadline of its own) cannot hold sign-out, or the sign-in after it.
 */
export function logout(): Promise<LogoutResult> {
  // Single-flight: a double click is one logout, not two racing each other.
  logoutInFlight ??= (async () => {
    authEpoch += 1
    signedOut = true
    sessionUserId = null
    setAccessToken(null)

    const deadline = new AbortController()
    const timer = setTimeout(() => deadline.abort(), LOGOUT_TIMEOUT_MS)
    let revoked = false
    try {
      await Promise.race([refreshInFlight?.catch(() => null), whenAborted(deadline.signal)])
      if (deadline.signal.aborted) return { revoked }
      const res = await withRefreshLock(
        () => send(apiUrl('/api/auth/logout'), { method: 'POST', credentials: 'include', signal: deadline.signal }),
        deadline.signal,
      )
      revoked = res.ok
    } catch {
      // Still signed out here — the user asked to leave, and stranding them
      // in the app helps nobody. The result says the cookie may have survived.
    } finally {
      clearTimeout(timer)
    }
    return { revoked }
  })().finally(() => {
    logoutInFlight = null
  })
  return logoutInFlight
}

function whenAborted(signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => signal.addEventListener('abort', () => resolve(), { once: true }))
}

/**
 * Sign in (login or register). Waits for any logout, then runs under the
 * cross-tab lock after any refresh in flight: a refresh landing after the
 * login would put the previous account's rotated cookie over the new one,
 * and the tab would quietly switch back at its next refresh.
 */
export async function authenticate(path: string, body: object): Promise<AuthResponse> {
  // A logout landing after the login would delete the cookie it just set.
  // (A late refresh's token is discarded by the epoch; its cookie is not,
  // which is what the lock is for.)
  await logoutInFlight
  await refreshInFlight?.catch(() => null)
  // Bounded like refresh: it holds the lock every tab's boot waits on.
  return withRefreshLock(() => api<AuthResponse>(path, { method: 'POST', body, timeoutMs: REFRESH_TIMEOUT_MS }))
}

// ---------------------------------------------------------------------------
// Requests

// Their 401 means "wrong credentials" or "no cookie", not "token expired" —
// retrying them through a refresh would be meaningless or recursive.
const NO_REFRESH_RETRY = new Set([
  '/api/auth/login',
  '/api/auth/register',
  '/api/auth/refresh',
  '/api/auth/logout',
])

interface RequestOptions {
  method?: string
  body?: unknown
  signal?: AbortSignal
  /** Give up after this long, as a NetworkError. */
  timeoutMs?: number
}

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, signal, timeoutMs } = options

  const attempt = (token: string | null) =>
    send(
      apiUrl(path),
      {
      method,
      signal,
      // The refresh cookie is path-scoped to /api/auth, so this attaches it
      // only where it is read.
      credentials: 'include',
      headers: {
        ...(body !== undefined && { 'Content-Type': 'application/json' }),
        ...(token && { Authorization: `Bearer ${token}` }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      },
      timeoutMs,
    )

  const sent = accessToken
  // The session this request was written for. If it has changed by the time
  // a 401 comes back — a sign-out, a sign-in, another account picked up by a
  // refresh — the request is refused, never replayed under the new one.
  const sentEpoch = authEpoch
  const sessionChanged = () =>
    new ApiError(401, 'Your session changed while this was being sent. Try again.')
  let res = await attempt(sent)

  if (res.status === 401 && !NO_REFRESH_RETRY.has(path)) {
    if (authEpoch !== sentEpoch) throw sessionChanged()
    // Another request may already have refreshed while this one was in
    // flight; reuse its token rather than rotating the cookie again.
    let fresh: string | null
    if (accessToken && accessToken !== sent) {
      fresh = accessToken
    } else {
      const refreshed = await refreshDetailed()
      // Written for one account, it must not be replayed as another.
      if (refreshed.userChanged || authEpoch !== sentEpoch) throw sessionChanged()
      fresh = refreshed.token
    }
    if (fresh === null) throw new ApiError(401, 'Your session has ended. Sign in again.')
    res = await attempt(fresh)
    if (res.status === 401 && authEpoch === sentEpoch) {
      // A token minted a moment ago is still refused: the account is gone.
      setAccessToken(null)
      onAuthLost?.()
    }
  }

  if (!res.ok) throw new ApiError(res.status, await readDetail(res))
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}
