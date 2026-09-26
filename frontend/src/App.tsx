import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Navigate, Route, Routes, useParams } from 'react-router-dom'
import { Toaster } from 'sonner'

import { ApiError } from '@/api/client'
import { AppShell } from '@/app/AppShell'
import { LoginPage, RegisterPage } from '@/features/auth/AuthPages'
import { AuthProvider } from '@/features/auth/AuthProvider'
import { ProtectedRoute } from '@/features/auth/ProtectedRoute'
import { SettingsPage } from '@/features/settings/SettingsPage'
import { TaskDetailPage } from '@/features/tasks/TaskDetailPage'
import { TaskListPage } from '@/features/tasks/TaskListPage'

/** Keyed by category, so a half-typed task, an open "Done" section or a
 *  pending delete prompt never carries over to the next category. */
function TaskListRoute() {
  const { categoryId } = useParams()
  return <TaskListPage key={categoryId ?? 'all'} />
}

const TRANSIENT_4XX = new Set([408, 429])

function isPermanent(error: unknown): boolean {
  return error instanceof ApiError && error.status < 500 && !TRANSIENT_4XX.has(error.status)
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // A 4xx will not change on retry — except a timeout or a rate limit.
      // A network blip or a 5xx might.
      retry: (failures, error) => failures < 2 && !isPermanent(error),
      staleTime: 10_000,
    },
  },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/register" element={<RegisterPage />} />
            <Route element={<ProtectedRoute />}>
              <Route element={<AppShell />}>
                <Route index element={<TaskListRoute />} />
                <Route path="c/:categoryId" element={<TaskListRoute />} />
                <Route path="tasks/:taskId" element={<TaskDetailPage />} />
                <Route path="settings" element={<SettingsPage />} />
              </Route>
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </AuthProvider>
      </BrowserRouter>
      <Toaster
        position="bottom-center"
        toastOptions={{
          classNames: {
            toast: '!bg-popover !text-popover-foreground !border-border !font-sans',
            description: '!text-muted-foreground',
          },
        }}
      />
    </QueryClientProvider>
  )
}
