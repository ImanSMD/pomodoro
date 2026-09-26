import { useId, useState, type FormEvent, type ReactNode } from 'react'
import { Link, Navigate, useLocation } from 'react-router-dom'

import { describeError } from '@/api/client'
import { Mark } from '@/components/Mark'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { TimerRing } from '@/features/timer/TimerRing'

import { useAuth } from './AuthProvider'
import { safeRedirect } from './redirect'

// Matches PASSWORD_MIN_LENGTH in backend/app/schemas/auth.py.
const PASSWORD_MIN_LENGTH = 8

function AuthLayout({ title, lede, children }: { title: string; lede: string; children: ReactNode }) {
  return (
    <div className="grid min-h-dvh lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
      <main className="flex flex-col px-6 py-8 sm:px-12">
        <div className="text-foreground flex items-center gap-2 font-semibold">
          <Mark />
          Pomodoro
        </div>
        <div className="my-auto w-full max-w-sm py-12">
          <h1 className="text-3xl font-semibold tracking-tight">{title}</h1>
          <p className="text-muted-foreground mt-2">{lede}</p>
          <div className="mt-8">{children}</div>
        </div>
      </main>
      {/* The product, not an illustration of it: the same bezel the timer uses. */}
      <aside
        aria-hidden="true"
        className="bg-focus-tint hidden place-items-center lg:grid"
      >
        <div className="w-[min(420px,70%)]">
          <TimerRing plannedMinutes={25} remainingMs={17 * 60_000 + 20_000} kind="work" />
        </div>
      </aside>
    </div>
  )
}

function Field({
  label,
  hint,
  ...props
}: { label: string; hint?: string } & React.ComponentProps<typeof Input>) {
  const id = useId()
  return (
    <div className="grid gap-2">
      <Label htmlFor={id}>{label}</Label>
      <Input id={id} className="h-11" aria-describedby={hint ? `${id}-hint` : undefined} {...props} />
      {hint && (
        <p id={`${id}-hint`} className="text-muted-foreground text-sm">
          {hint}
        </p>
      )}
    </div>
  )
}

function useRedirectTarget(): string {
  const location = useLocation()
  return safeRedirect((location.state as { from?: string } | null)?.from)
}

function FormError({ message }: { message: string | null }) {
  if (!message) return null
  return (
    <p role="alert" className="text-destructive text-sm">
      {message}
    </p>
  )
}

/** Submission for both auth forms: pending while the request runs, the
 *  server's reason on failure. Success needs no handling here — the auth
 *  state changes and the page redirects. */
function useAuthForm(submit: (form: FormData) => Promise<void>) {
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    setPending(true)
    setError(null)
    try {
      await submit(form)
    } catch (e) {
      setError(describeError(e))
      setPending(false)
    }
  }

  return { error, pending, onSubmit }
}

export function LoginPage() {
  const { state, login } = useAuth()
  const target = useRedirectTarget()
  const { error, pending, onSubmit } = useAuthForm((form) =>
    login(String(form.get('email')), String(form.get('password'))),
  )

  if (state.status === 'authenticated') return <Navigate to={target} replace />

  return (
    <AuthLayout title="Sign in" lede="Pick up where you left off.">
      <form onSubmit={onSubmit} className="grid gap-5">
        <Field label="Email" name="email" type="email" autoComplete="email" required autoFocus />
        <Field label="Password" name="password" type="password" autoComplete="current-password" required />
        <FormError message={error} />
        <Button type="submit" size="lg" className="h-11" disabled={pending}>
          {pending ? 'Signing in…' : 'Sign in'}
        </Button>
      </form>
      <p className="text-muted-foreground mt-8 text-sm">
        New here?{' '}
        <Link to="/register" state={{ from: target }} className="text-foreground font-medium underline underline-offset-4">
          Create an account
        </Link>
      </p>
    </AuthLayout>
  )
}

export function RegisterPage() {
  const { state, register } = useAuth()
  const target = useRedirectTarget()
  const { error, pending, onSubmit } = useAuthForm((form) =>
    register(String(form.get('email')), String(form.get('password')), String(form.get('display_name')).trim()),
  )

  if (state.status === 'authenticated') return <Navigate to={target} replace />

  return (
    <AuthLayout title="Create your account" lede="Tasks, timers and your focus history in one place.">
      <form onSubmit={onSubmit} className="grid gap-5">
        <Field label="Name" name="display_name" autoComplete="name" required maxLength={100} autoFocus />
        <Field label="Email" name="email" type="email" autoComplete="email" required maxLength={320} />
        <Field
          label="Password"
          name="password"
          type="password"
          autoComplete="new-password"
          required
          minLength={PASSWORD_MIN_LENGTH}
          maxLength={128}
          hint={`At least ${PASSWORD_MIN_LENGTH} characters.`}
        />
        <FormError message={error} />
        <Button type="submit" size="lg" className="h-11" disabled={pending}>
          {pending ? 'Creating account…' : 'Create account'}
        </Button>
      </form>
      <p className="text-muted-foreground mt-8 text-sm">
        Already have an account?{' '}
        <Link to="/login" state={{ from: target }} className="text-foreground font-medium underline underline-offset-4">
          Sign in
        </Link>
      </p>
    </AuthLayout>
  )
}
