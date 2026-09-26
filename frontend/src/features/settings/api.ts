import { useQuery } from '@tanstack/react-query'

import { api } from '@/api/client'

export interface UserSettings {
  timezone: string
  calendar_pref: 'jalali' | 'gregorian'
  default_work_minutes: number
  default_break_minutes: number
  long_break_minutes: number
  rounds_before_long_break: number
  auto_start_breaks: boolean
  sound_enabled: boolean
  notifications_enabled: boolean
}

export const settingsKey = ['settings'] as const

export function useSettings() {
  return useQuery({
    queryKey: settingsKey,
    queryFn: ({ signal }) => api<UserSettings>('/api/settings', { signal }),
  })
}
