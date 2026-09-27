import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import type { Session, Task } from '@/api/types'

import { TimerStage } from './TimerStage'

const NOW = Date.parse('2026-09-25T10:00:00Z')

function renderStage(session: Session, tasksResponses: Task[][], activeResponses: (Session | null)[] = [session]) {
  let taskFetches = 0
  let activeFetches = 0
  const fetchMock = vi.fn(async (url: string, _init?: RequestInit) => {
      if (url.includes('/api/sessions/active')) {
        const current = activeResponses[Math.min(activeFetches, activeResponses.length - 1)]
        activeFetches += 1
        return Response.json({ session: current, server_now: new Date(NOW).toISOString() })
      }
      if (url.includes('/api/tasks')) {
        const body = tasksResponses[Math.min(taskFetches, tasksResponses.length - 1)]
        taskFetches += 1
        return Response.json(body)
      }
      return Response.json([])
    })
  vi.stubGlobal('fetch', fetchMock)
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <TimerStage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { fetchMock }
}

const task: Task = {
  id: 't9',
  category_id: null,
  title: 'Started on another device',
  description: null,
  status: 'active',
  position: 0,
  archived_at: null,
  completed_at: null,
  created_at: '2026-09-25T09:00:00Z',
}

function session(startedAgoMinutes: number): Session {
  return {
    id: 's1',
    task_id: 't9',
    kind: 'work',
    status: 'running',
    started_at: new Date(NOW - startedAgoMinutes * 60_000).toISOString(),
    ended_at: null,
    planned_minutes: 25,
    duration_seconds: null,
    note: null,
  }
}

describe('TimerStage', () => {
  it('fetches tasks again when the running task is one this tab has never seen', async () => {
    vi.useFakeTimers({ now: NOW, shouldAdvanceTime: true })
    renderStage(session(1), [[], [task]])
    expect(await screen.findByText('Started on another device')).toBeTruthy()
    vi.useRealTimers()
  })

  it('offers "record all" only while that is within the 24-hour limit fix-end enforces', async () => {
    vi.useFakeTimers({ now: NOW, shouldAdvanceTime: true })
    renderStage(session(3 * 24 * 60), [[task]])
    expect(await screen.findByText('This session ended while you were away')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Record 25 minutes' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Record all/ })).toBeNull()
    vi.useRealTimers()
  })

  it('does not rewrite a session another device settled while this one showed it as overdue', async () => {
    vi.useFakeTimers({ now: NOW, shouldAdvanceTime: true })
    // First load: overdue. By the time the user clicks, it has been settled.
    const { fetchMock } = renderStage(session(9 * 60), [[task]], [session(9 * 60), null])
    fireEvent.click(await screen.findByRole('button', { name: 'Record 25 minutes' }))
    await screen.findByText('Pick a task and press Start.')
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(false)
  })

  it('still offers "record all" for a gap under 24 hours', async () => {
    vi.useFakeTimers({ now: NOW, shouldAdvanceTime: true })
    renderStage(session(9 * 60), [[task]])
    expect(await screen.findByRole('button', { name: /Record all/ })).toBeTruthy()
    vi.useRealTimers()
  })

  it('records all of it through fix-end — /complete refuses a session this far past its end', async () => {
    vi.useFakeTimers({ now: NOW, shouldAdvanceTime: true })
    // Nine hours in, on a 25-minute plan: well past the server's COMPLETE_GRACE.
    const { fetchMock } = renderStage(session(9 * 60), [[task]])
    fireEvent.click(await screen.findByRole('button', { name: /Record all/ }))

    await vi.waitFor(() => expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(true))
    const [url, init] = fetchMock.mock.calls.find(([, init]) => init?.method === 'PATCH')!
    expect(url).toMatch(/\/api\/sessions\/s1$/)
    // Nine hours, as minutes, measured on the server's clock.
    expect(JSON.parse(String(init?.body)).duration_minutes).toBeCloseTo(9 * 60, 0)
    expect(fetchMock.mock.calls.some(([u]) => String(u).endsWith('/complete'))).toBe(false)
  })
})
