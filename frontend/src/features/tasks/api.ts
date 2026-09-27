import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api, ApiError } from '@/api/client'
import type { Task } from '@/api/types'
import { removeById, upsertById } from '@/lib/listCache'

export const tasksKey = ['tasks'] as const

/** All of this user's tasks, archived included, filtered on the client.
 *  One cache means the timer can always name a running session's task, even
 *  when the list on screen is filtered to another category. */
export function useTasks() {
  return useQuery({
    queryKey: tasksKey,
    queryFn: ({ signal }) => api<Task[]>('/api/tasks?include_archived=true', { signal }),
  })
}

/** One task out of the shared list, with that list's loading state. */
export function useTask(id: string | undefined) {
  const query = useTasks()
  const task = id ? query.data?.find((t) => t.id === id) : undefined
  return { task, isPending: query.isPending, isFetching: query.isFetching, error: query.error }
}

export type TaskAction =
  | { type: 'create'; title: string; categoryId: string | null }
  | { type: 'move'; id: string; categoryId: string | null }
  | { type: 'complete' | 'reopen' | 'archive' | 'restore' | 'delete'; id: string }

function runTaskAction(action: TaskAction): Promise<Task | undefined> {
  switch (action.type) {
    case 'create':
      return api('/api/tasks', {
        method: 'POST',
        body: { title: action.title, category_id: action.categoryId },
      })
    case 'move':
      return api(`/api/tasks/${action.id}`, {
        method: 'PATCH',
        body: { category_id: action.categoryId },
      })
    case 'delete':
      return api(`/api/tasks/${action.id}`, { method: 'DELETE' })
    default:
      return api(`/api/tasks/${action.id}/${action.type}`, { method: 'POST' })
  }
}

export function useTaskAction() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: runTaskAction,
    onSuccess: (task, action) => {
      // Apply the server's row straight away so a tick feels instant, then
      // refetch: restore and create move positions the client cannot know.
      queryClient.setQueryData<Task[]>(tasksKey, (list) =>
        action.type === 'delete' ? removeById(list, action.id) : upsertById(list, task),
      )
      void queryClient.invalidateQueries({ queryKey: tasksKey })
    },
    onError: (error) => {
      // 409 from delete/archive: a session is running on the task, started
      // somewhere this tab has not heard about yet. Show it.
      if (error instanceof ApiError && error.status === 409) {
        void queryClient.invalidateQueries({ queryKey: ['session', 'active'] })
      }
    },
  })
}
