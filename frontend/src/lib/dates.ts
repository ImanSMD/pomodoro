import type { User } from '@/api/types'

const formatters = new Map<string, Intl.DateTimeFormat>()

function formatter(locale: string, timeZone: string): Intl.DateTimeFormat {
  const key = `${locale}|${timeZone}`
  let cached = formatters.get(key)
  if (!cached) {
    const options: Intl.DateTimeFormatOptions = { dateStyle: 'medium', timeStyle: 'short' }
    try {
      cached = new Intl.DateTimeFormat(locale, { ...options, timeZone })
    } catch {
      // An engine without this zone's data: the browser's own zone beats nothing.
      cached = new Intl.DateTimeFormat(locale, options)
    }
    formatters.set(key, cached)
  }
  return cached
}

/** A date and time in the user's own zone and calendar (Jalali by default). */
export function formatDateTime(iso: string, user: Pick<User, 'timezone' | 'calendar_pref'>): string {
  const locale = user.calendar_pref === 'jalali' ? 'en-u-ca-persian' : 'en'
  return formatter(locale, user.timezone).format(new Date(iso))
}

export function formatDuration(seconds: number): string {
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes} min`
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  return m ? `${h} h ${m} min` : `${h} h`
}
