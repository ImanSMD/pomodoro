import { useQuery } from '@tanstack/react-query'
import { ArrowLeft } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'

import { api, describeError } from '@/api/client'
import type { Session } from '@/api/types'
import { Page } from '@/app/Page'
import { useCurrentUser } from '@/features/auth/AuthProvider'
import { useCategoryLookup } from '@/features/categories/api'
import { CategoryDot } from '@/features/categories/CategoryDot'
import { KIND_LABEL } from '@/features/timer/countdown'
import { sessionListsKey } from '@/features/timer/session'
import { formatDateTime, formatDuration } from '@/lib/dates'

import { useTask } from './api'

/** A stub: phase 2 builds the real task page (todos, notes, tags). For now
 *  it names the task and lists the sessions spent on it. */
export function TaskDetailPage() {
  const { taskId } = useParams()
  const user = useCurrentUser()
  const { task, isPending, error } = useTask(taskId)
  const categories = useCategoryLookup()
  const sessions = useQuery({
    queryKey: [...sessionListsKey, 'task', taskId],
    queryFn: ({ signal }) => api<Session[]>(`/api/sessions?task_id=${taskId}&limit=20`, { signal }),
    enabled: !!task,
  })

  const category = task?.category_id ? categories.get(task.category_id) : undefined

  return (
    <Page>
      <Link to="/" className="text-muted-foreground hover:text-foreground inline-flex min-h-10 items-center gap-2 text-sm">
        <ArrowLeft className="size-4" aria-hidden="true" />
        All tasks
      </Link>

      {isPending ? (
        <p className="text-muted-foreground mt-6 text-sm">Loading…</p>
      ) : error && !task ? (
        <p role="alert" className="text-destructive mt-6 text-sm">
          Couldn’t load this task: {describeError(error)}
        </p>
      ) : !task ? (
        <p className="mt-6">This task doesn’t exist, or it was deleted.</p>
      ) : (
        <>
          <h1 className="mt-4 text-2xl font-semibold tracking-tight sm:text-3xl">{task.title}</h1>
          <p className="text-muted-foreground mt-2 flex items-center gap-2 text-sm">
            {category ? (
              <>
                <CategoryDot category={category} />
                {category.name}
                {category.archived_at && ' (archived)'}
              </>
            ) : (
              'No category'
            )}
          </p>
          {task.description && <p className="mt-6 whitespace-pre-wrap">{task.description}</p>}

          <h2 className="mt-10 text-lg font-medium">Sessions</h2>
          {sessions.isPending ? (
            <p className="text-muted-foreground mt-3 text-sm">Loading sessions…</p>
          ) : sessions.error ? (
            <p role="alert" className="text-destructive mt-3 text-sm">
              Couldn’t load sessions: {describeError(sessions.error)}
            </p>
          ) : sessions.data.length ? (
            <ul className="mt-3 grid divide-y">
              {sessions.data.map((s) => (
                <li key={s.id} className="flex items-baseline justify-between gap-4 py-3 text-sm">
                  <span>
                    {KIND_LABEL[s.kind]}
                    <span className="text-muted-foreground"> {formatDateTime(s.started_at, user)}</span>
                  </span>
                  <span className="text-muted-foreground tabular-nums">
                    {s.status === 'running'
                      ? 'Running'
                      : s.status === 'cancelled'
                        ? 'Discarded'
                        : s.duration_seconds !== null
                          ? formatDuration(s.duration_seconds)
                          : ''}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-muted-foreground mt-3 text-sm">No sessions yet. Press Start on the task to begin one.</p>
          )}
        </>
      )}
    </Page>
  )
}
