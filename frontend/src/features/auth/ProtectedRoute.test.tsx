import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { beginSession, setAccessToken } from '@/api/client'

import { AuthProvider, useAuth } from './AuthProvider'
import { ProtectedRoute } from './ProtectedRoute'

let auth: ReturnType<typeof useAuth>

function Deep() {
  auth = useAuth()
  return <p>deep page</p>
}

function Login() {
  const from = (useLocation().state as { from?: string } | null)?.from
  return <p>login, from: {from ?? 'nowhere'}</p>
}

const json = (status: number, body: unknown) => Response.json(body, { status })

function renderAt(path: string) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={[path]}>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route element={<ProtectedRoute />}>
              <Route path="/tasks/:id" element={<Deep />} />
            </Route>
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('ProtectedRoute', () => {
  it('after a deliberate sign-out, the next sign-in does not inherit the old page', async () => {
    beginSession('', '')
    setAccessToken(null)
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url.endsWith('/refresh')) {
          return json(200, { access_token: 'x', token_type: 'bearer', user: { id: 'x', display_name: 'X' } })
        }
        return new Response(null, { status: 204 })
      }),
    )
    renderAt('/tasks/t1')
    await screen.findByText('deep page')

    await act(async () => {
      await auth.logout()
    })
    expect(screen.getByText('login, from: nowhere')).toBeTruthy()
  })

  it('a deep link while signed out comes back to it after sign-in', async () => {
    beginSession('', '')
    setAccessToken(null)
    vi.stubGlobal('fetch', vi.fn(async () => json(401, { detail: 'Invalid refresh token' })))
    renderAt('/tasks/t1')
    expect(await screen.findByText('login, from: /tasks/t1', {}, { timeout: 3_000 })).toBeTruthy()
  })
})
