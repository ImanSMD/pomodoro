import { Page } from '@/app/Page'
import { describeError } from '@/api/client'
import { useCurrentUser } from '@/features/auth/AuthProvider'

import { useSettings } from './api'

/** A stub: shows the defaults new sessions are started with. */
export function SettingsPage() {
  const user = useCurrentUser()
  const { data: settings, error } = useSettings()

  const rows: [string, string][] = settings
    ? [
        ['Focus length', `${settings.default_work_minutes} minutes`],
        ['Short break', `${settings.default_break_minutes} minutes`],
        ['Long break', `${settings.long_break_minutes} minutes`],
        ['Long break after', `${settings.rounds_before_long_break} focus sessions`],
        ['Time zone', settings.timezone],
        ['Calendar', settings.calendar_pref === 'jalali' ? 'Jalali' : 'Gregorian'],
      ]
    : []

  return (
    <Page>
      <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Settings</h1>
      <p className="text-muted-foreground mt-1 text-sm">
        Signed in as {user.display_name} ({user.email})
      </p>
      {error && (
        <p role="alert" className="text-destructive mt-6 text-sm">
          Couldn’t load settings: {describeError(error)}
        </p>
      )}
      <dl className="mt-8 grid divide-y">
        {rows.map(([label, value]) => (
          <div key={label} className="flex justify-between gap-4 py-3 text-sm">
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="font-medium">{value}</dd>
          </div>
        ))}
      </dl>
      <p className="text-muted-foreground mt-6 text-sm">
        A running session keeps the length it started with, even if these change.
      </p>
    </Page>
  )
}
