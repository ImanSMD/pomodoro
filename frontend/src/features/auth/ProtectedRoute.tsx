import { Navigate, Outlet, useLocation } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { Mark } from '@/components/Mark'

import { useAuth } from './AuthProvider'

export function FullPageMessage({ children }: { children: React.ReactNode }) {
  return (
    <main className="grid min-h-dvh place-items-center px-6">
      <div className="flex max-w-sm flex-col items-center gap-4 text-center">{children}</div>
    </main>
  )
}

export function ProtectedRoute() {
  const { state, retry } = useAuth()
  const location = useLocation()

  if (state.status === 'loading') {
    return (
      <FullPageMessage>
        <Mark className="size-10 animate-pulse motion-reduce:animate-none" />
        <span className="sr-only">Loading</span>
      </FullPageMessage>
    )
  }
  if (state.status === 'signing-out') {
    return (
      <FullPageMessage>
        <Mark className="size-10 animate-pulse motion-reduce:animate-none" />
        <p className="text-lg font-medium">Signing out…</p>
      </FullPageMessage>
    )
  }
  if (state.status === 'unreachable') {
    return (
      <FullPageMessage>
        <Mark className="size-10" />
        <p className="text-lg font-medium">Can’t reach the server</p>
        <p className="text-muted-foreground text-sm">
          Your timer is safe — it runs on the server. Check the connection and try again.
        </p>
        <Button onClick={retry}>Try again</Button>
      </FullPageMessage>
    )
  }
  if (state.status === 'anonymous') {
    // Back to where they were after a lapsed session or a deep link — but not
    // after signing out on purpose, when the next person to sign in on this
    // browser may be someone else.
    const from = state.reason === 'signed-out' ? undefined : location.pathname + location.search
    return <Navigate to="/login" replace state={from ? { from } : undefined} />
  }
  // Keyed on the user: if another tab switches this browser to a different
  // account, everything here — cached views, the socket — starts over as them.
  return <Outlet key={state.user.id} />
}
