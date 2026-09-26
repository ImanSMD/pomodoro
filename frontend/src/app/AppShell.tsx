import { Outlet } from 'react-router-dom'

import { useSessionSocket } from '@/features/realtime/useSessionSocket'
import { TimerStage } from '@/features/timer/TimerStage'
import { useMediaQuery } from '@/lib/useMediaQuery'

import { MobileNav, Sidebar } from './Navigation'

/**
 *   phone    [header + category chips] / [timer] / [page]
 *   tablet   [sidebar] [timer / page]
 *   desktop  [sidebar] [page] [timer, full height]
 */
export function AppShell() {
  useSessionSocket()
  // One navigation tree, not two with one hidden: each subscribes to the
  // task and category queries and carries its own dialog. Tailwind's md.
  const wide = useMediaQuery('(min-width: 48rem)')

  return (
    <div className="min-h-dvh md:grid md:grid-cols-[232px_minmax(0,1fr)] md:grid-rows-[auto_1fr] lg:grid-cols-[232px_minmax(0,1fr)_minmax(360px,42%)] lg:grid-rows-1">
      {wide ? (
        // Spans both rows on tablet (timer above page), so it neither
        // stretches the timer's row to full height nor loses its stickiness.
        <aside className="sticky top-0 row-span-2 h-dvh border-r lg:row-span-1">
          <Sidebar />
        </aside>
      ) : (
        <MobileNav />
      )}
      {/* Timer first in source order on small screens, so it sits above the
          list; on desktop it moves to its own column on the right. */}
      <div className="lg:sticky lg:top-0 lg:order-last lg:h-dvh lg:border-l">
        <TimerStage />
      </div>
      <main className="min-w-0 md:col-start-2 md:row-start-2 lg:row-start-1">
        <Outlet />
      </main>
    </div>
  )
}
