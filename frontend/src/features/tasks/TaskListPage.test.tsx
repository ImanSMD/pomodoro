import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import type { Category, Session, Task } from '@/api/types'

import { TaskListPage } from './TaskListPage'

const category: Category = {
  id: 'c1',
  name: 'Thesis',
  color: '#2a8c84',
  icon: null,
  position: 0,
  archived_at: null,
  created_at: '2026-09-01T00:00:00Z',
}

function task(id: string, title: string): Task {
  return {
    id,
    category_id: 'c1',
    title,
    description: null,
    status: 'active',
    position: 0,
    archived_at: null,
    completed_at: null,
    created_at: '2026-09-01T00:00:00Z',
  }
}

const running: Session = {
  id: 's1',
  task_id: 't1',
  kind: 'work',
  status: 'running',
  started_at: '2026-09-25T10:00:00Z',
  ended_at: null,
  planned_minutes: 25,
  duration_seconds: null,
  note: null,
}

type Routes = Record<string, () => Promise<Response> | Response>

function renderAt(path: string, routes: Routes) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      const match = Object.keys(routes).find((prefix) => String(url).includes(prefix))
      return match ? routes[match]() : Response.json([])
    }),
  )
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/" element={<p>home</p>} />
          <Route path="/c/:categoryId" element={<TaskListPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('a category page before its category is known', () => {
  it('shows neither the "All tasks" view nor a form that would file tasks uncategorised', async () => {
    renderAt('/c/c1', {
      '/api/categories': () => new Promise<Response>(() => {}), // never settles
      '/api/tasks': () => Response.json([task('t1', 'Draft outline')]),
    })
    expect(await screen.findByText('Loading…')).toBeTruthy()
    expect(screen.queryByRole('heading', { name: 'All tasks' })).toBeNull()
    expect(screen.queryByLabelText('New task')).toBeNull()
  })

  it('says the categories failed to load rather than showing a mislabelled list', async () => {
    renderAt('/c/c1', {
      '/api/categories': () => Response.json({ detail: 'boom' }, { status: 500 }),
      '/api/tasks': () => Response.json([task('t1', 'Draft outline')]),
    })
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.queryByLabelText('New task')).toBeNull()
    expect(screen.queryByText('home')).toBeNull() // no redirect on an error
  })
})

describe('a failed background refetch', () => {
  it('keeps showing the tasks it already has', async () => {
    let taskFetches = 0
    const { QueryClient: QC } = await import('@tanstack/react-query')
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (String(url).includes('/api/categories')) return Response.json([category])
        if (String(url).includes('/api/tasks')) {
          taskFetches += 1
          return taskFetches === 1
            ? Response.json([task('t1', 'Draft outline')])
            : Response.json({ detail: 'boom' }, { status: 500 })
        }
        return Response.json([])
      }),
    )
    const client = new QC({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/c/c1']}>
          <Routes>
            <Route path="/c/:categoryId" element={<TaskListPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    )
    await screen.findByText('Draft outline')
    await act(async () => {
      await client.refetchQueries({ queryKey: ['tasks'] })
    })
    // Observers are notified asynchronously; wait for the note to render.
    expect((await screen.findByRole('status')).textContent).toMatch(/couldn’t refresh/i)
    expect(screen.getByText('Draft outline')).toBeTruthy()
  })
})

describe('the running task', () => {
  it('cannot be archived or deleted out from under its session', async () => {
    renderAt('/c/c1', {
      '/api/categories': () => Response.json([category]),
      '/api/tasks': () => Response.json([task('t1', 'Draft outline'), task('t2', 'Read paper')]),
      '/api/sessions/active': () =>
        Response.json({ session: running, server_now: '2026-09-25T10:01:00Z' }),
    })
    await screen.findByText('In focus now')
    // Nor marked done under it: the session would finish against a done task.
    // (Checked before the menu opens — an open menu hides the rest of the page.)
    expect(screen.getByRole('checkbox', { name: 'Mark Draft outline done' }).hasAttribute('disabled')).toBe(true)

    const trigger = screen.getByRole('button', { name: 'Draft outline options' })
    fireEvent.keyDown(trigger, { key: 'Enter' })

    const del = await screen.findByRole('menuitem', { name: /Delete/ })
    const archive = screen.getByRole('menuitem', { name: /Archive/ })
    expect(del.getAttribute('aria-disabled')).toBe('true')
    expect(archive.getAttribute('aria-disabled')).toBe('true')
  })

  it('leaves other tasks deletable', async () => {
    renderAt('/c/c1', {
      '/api/categories': () => Response.json([category]),
      '/api/tasks': () => Response.json([task('t1', 'Draft outline'), task('t2', 'Read paper')]),
      '/api/sessions/active': () =>
        Response.json({ session: running, server_now: '2026-09-25T10:01:00Z' }),
    })
    await screen.findByText('In focus now')

    fireEvent.keyDown(screen.getByRole('button', { name: 'Read paper options' }), { key: 'Enter' })
    const del = await screen.findByRole('menuitem', { name: /Delete/ })
    expect(del.getAttribute('aria-disabled')).toBeNull()
  })
})

describe('a delete the server refuses', () => {
  it('re-reads the active session: another device started one on this task', async () => {
    let activeReads = 0
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        const u = String(url)
        if (u.includes('/api/categories')) return Response.json([category])
        if (u.includes('/api/tasks') && init?.method === 'DELETE') {
          return Response.json({ detail: 'A session is running on this task. Finish or cancel it first.' }, { status: 409 })
        }
        if (u.includes('/api/tasks')) return Response.json([task('t1', 'Draft outline')])
        if (u.includes('/api/sessions/active')) {
          activeReads += 1
          return Response.json({ session: null, server_now: '2026-09-25T10:01:00Z' })
        }
        return Response.json([])
      }),
    )
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/c/c1']}>
          <Routes>
            <Route path="/c/:categoryId" element={<TaskListPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    )
    await screen.findByText('Draft outline')
    await vi.waitFor(() => expect(activeReads).toBe(1))

    fireEvent.keyDown(screen.getByRole('button', { name: 'Draft outline options' }), { key: 'Enter' })
    fireEvent.click(await screen.findByRole('menuitem', { name: /Delete/ }))
    fireEvent.click(await screen.findByRole('button', { name: 'Delete task' }))

    await vi.waitFor(() => expect(activeReads).toBe(2))
  })
})

