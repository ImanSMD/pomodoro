import { getAccessToken, refreshAccessToken, tokenExpiresAt, wsUrl } from '@/api/client'
import type { SessionEvent } from '@/api/types'

const EVENTS = new Set(['session.started', 'session.completed', 'session.cancelled'])

const BASE_DELAY_MS = 500
const MAX_DELAY_MS = 30_000
// A socket that stayed up this long was healthy; its close (usually the
// server ending it at token expiry) starts the backoff from scratch.
const HEALTHY_AFTER_MS = 10_000
// Refresh before connecting if the token has less than this left — the
// server would close the socket the moment it expires anyway.
const TOKEN_MARGIN_MS = 5_000

/** Exponential, capped, with jitter so tabs do not reconnect in lockstep. */
export function backoffDelay(attempt: number, random: () => number = Math.random): number {
  const base = Math.min(MAX_DELAY_MS, BASE_DELAY_MS * 2 ** attempt)
  return Math.round(base * (0.75 + random() * 0.5))
}

export function parseSessionEvent(raw: unknown): SessionEvent | null {
  if (typeof raw !== 'string') return null
  try {
    const message = JSON.parse(raw) as Partial<SessionEvent>
    return typeof message.event === 'string' && EVENTS.has(message.event)
      ? (message as SessionEvent)
      : null
  } catch {
    return null
  }
}

async function usableToken(): Promise<string | null> {
  const token = getAccessToken()
  const exp = token ? tokenExpiresAt(token) : null
  if (token && (exp === null || exp * 1000 - Date.now() > TOKEN_MARGIN_MS)) return token
  // Single-flight with the API client, so this never races an HTTP 401's
  // refresh. null means logged out, which the auth-lost handler deals with.
  return refreshAccessToken()
}

interface Handlers {
  onEvent: (event: SessionEvent) => void
  /** On every open, the first included: events may have been published
   *  while there was no socket to receive them. */
  onOpen: () => void
}

/**
 * Keep a socket open for as long as the caller wants one. The server closes
 * it when its access token expires, so every reconnect starts by making sure
 * the token is fresh. Returns a stop function.
 */
export function startSessionSocket({ onEvent, onOpen }: Handlers): () => void {
  let stopped = false
  let socket: WebSocket | null = null
  let connecting = false
  let timer: ReturnType<typeof setTimeout> | undefined
  let attempt = 0
  // Set when a handshake is refused. The usual cause is an expired token,
  // and the local expiry check cannot be trusted to notice: it reads the
  // browser clock, the server reads its own.
  let forceRefresh = false
  // The token a forced refresh produced. If that one is refused as well, the
  // failure is not about auth — a proxy without Upgrade, a network blip — and
  // refreshing on every retry would only rotate the cookie for nothing. So at
  // most one forced refresh per token; ordinary expiry still refreshes via
  // usableToken().
  let forcedToken: string | null = null

  const schedule = () => {
    if (stopped) return
    timer = setTimeout(connect, backoffDelay(attempt))
    attempt += 1
  }

  async function connect() {
    if (stopped || socket || connecting) return
    connecting = true
    let token: string | null
    try {
      if (forceRefresh) {
        token = await refreshAccessToken()
        forcedToken = token
      } else {
        token = await usableToken()
      }
      forceRefresh = false
    } catch {
      // Network trouble reaching /auth/refresh — try again later.
      connecting = false
      schedule()
      return
    }
    connecting = false
    if (stopped || !token) return

    const ws = new WebSocket(wsUrl(`/ws?token=${encodeURIComponent(token)}`))
    socket = ws
    let openedAt = 0

    ws.onopen = () => {
      openedAt = Date.now()
      // This token worked, so a later refusal of it is expiry again, not the
      // non-auth failure the once-per-token rule guards against.
      forcedToken = null
      onOpen()
    }
    ws.onmessage = (message) => {
      const event = parseSessionEvent(message.data)
      if (event) onEvent(event)
    }
    ws.onclose = () => {
      if (socket === ws) socket = null
      if (!openedAt && token !== forcedToken) forceRefresh = true
      if (openedAt && Date.now() - openedAt >= HEALTHY_AFTER_MS) attempt = 0
      schedule()
    }
  }

  // Coming back online: skip the rest of the backoff, and replace any socket
  // that exists — after a network change it may be dead without ever having
  // fired close (no close frame arrives, and the client sends nothing that
  // would fail). Detecting that in general would take a server ping.
  const onOnline = () => {
    if (connecting) return
    clearTimeout(timer)
    attempt = 0
    if (socket) {
      const stale = socket
      socket = null
      stale.onclose = null
      stale.close()
    }
    void connect()
  }
  window.addEventListener('online', onOnline)

  void connect()

  return () => {
    stopped = true
    clearTimeout(timer)
    window.removeEventListener('online', onOnline)
    socket?.close()
    socket = null
  }
}
