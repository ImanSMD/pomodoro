import { useMemo, useState } from 'react'
import { ChevronRight, LogOut, MoreHorizontal, Plus, Settings } from 'lucide-react'
import { NavLink } from 'react-router-dom'

import { Mark } from '@/components/Mark'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { useAuth, useCurrentUser } from '@/features/auth/AuthProvider'
import { archivedCategories, liveCategories, useCategories } from '@/features/categories/api'
import { CategoryDialog } from '@/features/categories/CategoryDialog'
import { CategoryDot } from '@/features/categories/CategoryDot'
import { useTasks } from '@/features/tasks/api'
import { cn } from '@/lib/utils'

function useOpenTaskCounts(): Map<string | 'all', number> {
  const { data: tasks } = useTasks()
  return useMemo(() => {
    const counts = new Map<string | 'all', number>()
    for (const task of tasks ?? []) {
      if (task.archived_at || task.status === 'done') continue
      counts.set('all', (counts.get('all') ?? 0) + 1)
      if (task.category_id) counts.set(task.category_id, (counts.get(task.category_id) ?? 0) + 1)
    }
    return counts
  }, [tasks])
}

const navItem = ({ isActive }: { isActive: boolean }) =>
  cn(
    'flex min-h-10 items-center gap-3 rounded-md px-3 text-sm transition-colors',
    'hover:bg-sidebar-accent focus-visible:ring-ring focus-visible:outline-none focus-visible:ring-2',
    isActive ? 'bg-sidebar-accent text-sidebar-accent-foreground font-medium' : 'text-sidebar-foreground/80',
  )

function Count({ n }: { n: number | undefined }) {
  return n ? <span className="text-muted-foreground ml-auto text-xs tabular-nums">{n}</span> : null
}

/** Desktop and tablet: a quiet column of places to go. Managing a category
 *  happens on its own page, so this stays navigation only. */
export function Sidebar() {
  const { data: categories } = useCategories()
  const counts = useOpenTaskCounts()
  const [creating, setCreating] = useState(false)
  const [showArchived, setShowArchived] = useState(false)
  const archived = archivedCategories(categories)

  return (
    <nav aria-label="Categories" className="bg-sidebar flex h-dvh flex-col gap-6 px-3 py-5">
      <div className="flex items-center gap-2 px-3 font-semibold">
        <Mark />
        Pomodoro
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto">
        <NavLink to="/" end className={navItem}>
          <span className="bg-foreground/25 size-2.5 rounded-full" aria-hidden="true" />
          All tasks
          <Count n={counts.get('all')} />
        </NavLink>
        {liveCategories(categories).map((category) => (
          <NavLink key={category.id} to={`/c/${category.id}`} className={navItem}>
            <CategoryDot category={category} />
            <span className="truncate">{category.name}</span>
            <Count n={counts.get(category.id)} />
          </NavLink>
        ))}
        <button
          type="button"
          onClick={() => setCreating(true)}
          className={cn(navItem({ isActive: false }), 'text-muted-foreground')}
        >
          <Plus className="size-4" aria-hidden="true" />
          New category
        </button>

        {archived.length > 0 && (
          <div className="mt-4">
            <button
              type="button"
              aria-expanded={showArchived}
              onClick={() => setShowArchived((v) => !v)}
              className={cn(navItem({ isActive: false }), 'text-muted-foreground w-full')}
            >
              <ChevronRight
                className={cn('size-4 transition-transform', showArchived && 'rotate-90')}
                aria-hidden="true"
              />
              Archived
              <Count n={archived.length} />
            </button>
            {showArchived &&
              archived.map((category) => (
                <NavLink
                  key={category.id}
                  to={`/c/${category.id}`}
                  className={(state) => cn(navItem(state), 'text-muted-foreground')}
                >
                  <CategoryDot category={category} />
                  <span className="truncate">{category.name}</span>
                </NavLink>
              ))}
          </div>
        )}
      </div>

      <div className="grid gap-1 border-t pt-4">
        <NavLink to="/settings" className={navItem}>
          <Settings className="size-4" aria-hidden="true" />
          Settings
        </NavLink>
        <AccountRow />
      </div>

      <CategoryDialog open={creating} onOpenChange={setCreating} />
    </nav>
  )
}

function AccountRow() {
  const user = useCurrentUser()
  const { logout } = useAuth()
  return (
    <div className="flex min-h-10 items-center gap-3 px-3 text-sm">
      <span className="min-w-0 flex-1 truncate" title={user.email}>
        {user.display_name}
      </span>
      <Button variant="ghost" size="sm" onClick={() => void logout()}>
        <LogOut aria-hidden="true" />
        Sign out
      </Button>
    </div>
  )
}

/** Phone: brand and account menu on one line, categories as a scrolling row. */
export function MobileNav() {
  const { data: categories } = useCategories()
  const { logout } = useAuth()
  const [creating, setCreating] = useState(false)

  const chip = ({ isActive }: { isActive: boolean }) =>
    cn(
      'flex h-10 shrink-0 items-center gap-2 rounded-full border px-4 text-sm whitespace-nowrap',
      'focus-visible:ring-ring focus-visible:outline-none focus-visible:ring-2',
      isActive ? 'border-foreground bg-foreground text-background' : 'border-border text-foreground/80',
    )

  return (
    <header className="bg-background/95 sticky top-0 z-20 border-b backdrop-blur">
      <div className="flex h-14 items-center justify-between px-4">
        <div className="flex items-center gap-2 font-semibold">
          <Mark />
          Pomodoro
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" className="size-11" aria-label="Menu">
              <MoreHorizontal />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-48">
            <DropdownMenuItem onSelect={() => setCreating(true)}>
              <Plus /> New category
            </DropdownMenuItem>
            <DropdownMenuItem asChild>
              <NavLink to="/settings">
                <Settings /> Settings
              </NavLink>
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => void logout()}>
              <LogOut /> Sign out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      <nav aria-label="Categories" className="flex gap-2 overflow-x-auto px-4 pb-3 [scrollbar-width:none]">
        <NavLink to="/" end className={chip}>
          All tasks
        </NavLink>
        {[...liveCategories(categories), ...archivedCategories(categories)].map((category) => (
          <NavLink
            key={category.id}
            to={`/c/${category.id}`}
            className={(state) => cn(chip(state), category.archived_at && !state.isActive && 'text-muted-foreground border-dashed')}
          >
            <CategoryDot category={category} />
            {category.name}
          </NavLink>
        ))}
      </nav>
      <CategoryDialog open={creating} onOpenChange={setCreating} />
    </header>
  )
}
