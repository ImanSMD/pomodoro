// Mirrors backend/app/schemas. UUIDs and datetimes arrive as strings.

export interface User {
  id: string
  email: string
  display_name: string
  timezone: string
  calendar_pref: 'jalali' | 'gregorian'
  created_at: string
}

export interface AuthResponse {
  access_token: string
  token_type: string
  user: User
}

export interface Category {
  id: string
  name: string
  color: string | null
  icon: string | null
  position: number
  archived_at: string | null
  created_at: string
}

export type TaskStatus = 'active' | 'done'

export interface Task {
  id: string
  category_id: string | null
  title: string
  description: string | null
  status: TaskStatus
  position: number
  archived_at: string | null
  completed_at: string | null
  created_at: string
}

export type SessionKind = 'work' | 'short_break' | 'long_break'
export type SessionStatus = 'running' | 'completed' | 'cancelled'

export interface Session {
  id: string
  task_id: string
  kind: SessionKind
  status: SessionStatus
  started_at: string
  ended_at: string | null
  planned_minutes: number
  duration_seconds: number | null
  note: string | null
}

export interface ActiveSessionResponse {
  session: Session | null
  server_now: string
}

export type SessionEventName = 'session.started' | 'session.completed' | 'session.cancelled'

export interface SessionEvent {
  event: SessionEventName
  data: Session
}
