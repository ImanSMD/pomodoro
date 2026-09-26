import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'

import type { Session, Task } from '@/api/types'
import { Button } from '@/components/ui/button'
import { useCategoryLookup } from '@/features/categories/api'
import { CategoryDot } from '@/features/categories/CategoryDot'
import { useSettings } from '@/features/settings/api'
import { tasksKey, useTask } from '@/features/tasks/api'
import { formatDuration } from '@/lib/dates'
import { toastError } from '@/lib/toastError'
import { cn } from '@/lib/utils'

import { readActiveSessionFresh, useActiveSession, useEndSession, type EndAction } from './session'
import { TimerRing } from './TimerRing'
import { isLongOverdue, useAutoComplete, useCountdown, useDocumentTitle } from './useCountdown'

/**
 * The running session's task. A session started elsewhere can name a task
 * this tab has never loaded — the socket's catch-up refetches sessions, not
 * tasks — so an unknown id triggers one refetch of the task list.
 */
function useSessionTask(taskId: string): Task | undefined {
  const { task, isFetching } = useTask(taskId)
  const queryClient = useQueryClient()
  const asked = useRef<string | null>(null)

  useEffect(() => {
    if (task || isFetching || asked.current === taskId) return
    asked.current = taskId
    void queryClient.invalidateQueries({ queryKey: tasksKey })
  }, [task, isFetching, taskId, queryClient])

  return task
}

function TaskLine({ task }: { task: Task | undefined }) {
  const categories = useCategoryLookup()
  if (!task) return null
  const category = task.category_id ? categories.get(task.category_id) : undefined
  return (
    <Link
      to={`/tasks/${task.id}`}
      className="hover:text-foreground flex max-w-full items-center justify-center gap-2 text-lg underline-offset-4 hover:underline"
    >
      {category && <CategoryDot category={category} />}
      <span className="truncate">{task.title}</span>
    </Link>
  )
}

/**
 * The timer. Mounted once in the shell, so the countdown, the completion
 * claim and the tab title keep running whichever page is open.
 */
export function TimerStage() {
  const { data } = useActiveSession()
  const session = data?.session ?? null
  // server − browser clock, refreshed with every fetch of the active session.
  const offsetMs = data?.offsetMs ?? 0
  const remaining = useCountdown(session, offsetMs)
  const overdue = isLongOverdue(remaining)
  useAutoComplete(session, remaining)
  // A long-overdue session is waiting on a decision, not counting down.
  useDocumentTitle(overdue ? null : session, overdue ? null : remaining)

  return (
    <section
      id="timer"
      aria-label="Timer"
      className={cn(
        'flex scroll-mt-28 flex-col items-center justify-center px-6 transition-colors duration-500 motion-reduce:transition-none lg:h-dvh lg:py-12',
        // Idle on a phone it is one line of guidance; running, it earns the room.
        session ? 'py-8' : 'py-5',
        session?.kind === 'work' && 'bg-focus-tint',
        session && session.kind !== 'work' && 'bg-rest-tint',
      )}
    >
      {session && remaining !== null && overdue ? (
        <OverdueSession key={session.id} session={session} overdueMs={-remaining} />
      ) : session && remaining !== null ? (
        // Keyed: a pending "Discard?" must never carry over to a different
        // session that replaced this one underneath it.
        <Running key={session.id} session={session} remaining={Math.max(0, remaining)} />
      ) : (
        <Idle />
      )}
    </section>
  )
}

/** Ending a session, with one way of reporting that it did not work. */
function useEndSessionWithToast() {
  const end = useEndSession()
  const run = (vars: EndAction) =>
    end.mutate(vars, {
      onError: toastError('Couldn’t update the session'),
    })
  return { run, isPending: end.isPending }
}

function Running({ session, remaining }: { session: Session; remaining: number }) {
  const task = useSessionTask(session.task_id)
  const end = useEndSessionWithToast()
  const [confirmingDiscard, setConfirmingDiscard] = useState(false)

  const finish = (action: 'complete' | 'cancel') => end.run({ sessionId: session.id, action })

  return (
    <div className="flex w-full max-w-md flex-col items-center gap-6 lg:gap-8">
      <div className="w-[min(100%,68vw)] sm:w-[min(100%,360px)] lg:w-full">
        <TimerRing plannedMinutes={session.planned_minutes} remainingMs={remaining} kind={session.kind} />
      </div>
      <TaskLine task={task} />

      {confirmingDiscard ? (
        <div className="flex flex-col items-center gap-3 text-center" role="group" aria-label="Discard session">
          <p className="text-muted-foreground text-sm">
            Discard this session? The time won’t count toward your focus.
          </p>
          <div className="flex gap-2">
            <Button variant="ghost" className="h-11" onClick={() => setConfirmingDiscard(false)} autoFocus>
              Keep going
            </Button>
            <Button variant="destructive" className="h-11" onClick={() => finish('cancel')} disabled={end.isPending}>
              Discard
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex gap-2">
          <Button variant="outline" className="h-11 px-5" onClick={() => finish('complete')} disabled={end.isPending}>
            Finish now
          </Button>
          <Button variant="ghost" className="h-11 px-5" onClick={() => setConfirmingDiscard(true)} disabled={end.isPending}>
            Discard
          </Button>
        </div>
      )}
    </div>
  )
}

/**
 * A session that ran out long ago with nobody watching. Completing it would
 * count the whole gap as focus, so the user decides what it was.
 */
// Mirrors MAX_SESSION_MINUTES in backend/app/schemas/session.py, which
// fix-end enforces. /complete does not, so "record all" must not be offered
// past it: it would store more than any session is allowed to be.
const MAX_SESSION_SECONDS = 24 * 60 * 60

function OverdueSession({ session, overdueMs }: { session: Session; overdueMs: number }) {
  const task = useSessionTask(session.task_id)
  const end = useEndSessionWithToast()
  const queryClient = useQueryClient()
  const [checking, setChecking] = useState(false)
  const elapsedSeconds = session.planned_minutes * 60 + Math.floor(overdueMs / 1000)

  // This screen can be stale — a tab waking from sleep shows its cached
  // session before the refetch lands — and fix-end, unlike complete, will
  // rewrite a session that is already completed. So confirm it is still this
  // one, still running, before settling it; if not, the refetch shows what
  // actually happened.
  const settle = async (vars: EndAction) => {
    setChecking(true)
    try {
      const current = await readActiveSessionFresh(queryClient)
      if (current.session?.id === session.id) end.run(vars)
    } catch (error) {
      toastError('Couldn’t update the session')(error)
    } finally {
      setChecking(false)
    }
  }
  const busy = checking || end.isPending

  return (
    <div className="flex w-full max-w-md flex-col items-center gap-6 text-center">
      <div>
        <p className="text-xl font-medium">This session ended while you were away</p>
        <p className="text-muted-foreground mt-2">
          It was planned for {session.planned_minutes} minutes and ran out {formatDuration(overdueMs / 1000)} ago.
          How much of it should count?
        </p>
      </div>
      <TaskLine task={task} />
      <div className="flex flex-wrap justify-center gap-2">
        <Button
          className="h-11 px-5"
          disabled={busy}
          onClick={() => settle({ sessionId: session.id, action: 'record', minutes: session.planned_minutes })}
        >
          Record {session.planned_minutes} minutes
        </Button>
        {elapsedSeconds <= MAX_SESSION_SECONDS && (
          <Button
            variant="outline"
            className="h-11 px-5"
            disabled={busy}
            onClick={() => settle({ sessionId: session.id, action: 'complete' })}
          >
            Record all {formatDuration(elapsedSeconds)}
          </Button>
        )}
        <Button
          variant="ghost"
          className="h-11 px-5"
          disabled={busy}
          onClick={() => settle({ sessionId: session.id, action: 'cancel' })}
        >
          Discard
        </Button>
      </div>
    </div>
  )
}

function Idle() {
  const { data: settings } = useSettings()
  const workMinutes = settings?.default_work_minutes ?? 25

  return (
    <div className="flex w-full max-w-md flex-col items-center gap-6 text-center lg:gap-8">
      <div className="hidden w-full lg:block">
        <TimerRing plannedMinutes={workMinutes} remainingMs={workMinutes * 60_000} kind="work" idle />
      </div>
      <p className="text-muted-foreground">Pick a task and press Start.</p>
    </div>
  )
}
