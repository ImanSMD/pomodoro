# Pomodoro: CLI → full-stack web app

## Context

Today the entire product is `pomodoro.py` — a 356-line argparse CLI that appends session rows to
`df_pomodoro.csv`, with Jalali dates packed as `YYYYMMDD` integers and "a task is running" defined
implicitly as *the last CSV row whose `mins` is NaN*. It works, but it is single-machine,
single-user, has no real timer (only start/end wall-clock stamps), no structure above a free-text
task string, and no way to see anything but a flat `tail` of rows.

The goal is a real application: FastAPI + Postgres backend, React frontend, Docker Compose for dev
and production. Beyond porting what exists, this adds a live server-authoritative timer, a
Category → Task → Todo hierarchy with tags, per-task timer settings, soft delete + archive with an
audit log, an analytics dashboard, a calendar view with a Jalali/Gregorian toggle, and goals with
streaks — for multiple users with accounts.

**Decisions already made:** multi-user with JWT auth · fresh database, no CSV import · UTC
`timestamptz` storage with a per-user calendar-display toggle · server-authoritative timer synced
over WebSocket · separate dev and production compose files · delivered in phases that each leave a
runnable app.

`pomodoro.py` stays in the repo untouched, as reference and as a record of the original tool.

---

## Stack

| Layer | Choice | Notes |
|---|---|---|
| API | FastAPI, Pydantic v2 | async throughout |
| ORM | SQLAlchemy 2.0 async + asyncpg | typed `Mapped[]` declarative models |
| Migrations | Alembic | autogenerate, one revision per phase |
| DB | Postgres 16 | `citext` for emails, `jsonb` for audit payloads |
| Auth | JWT access + refresh, Argon2 via passlib | refresh token in an httpOnly cookie |
| Frontend | React 18 + Vite + TypeScript | |
| Server state | TanStack Query | cache invalidation on WS events |
| UI | Tailwind + shadcn/ui | |
| Charts | Recharts | |
| Jalali | `date-fns-jalali` | display only; never in stored values |
| Tests | pytest + httpx `AsyncClient` | throwaway Postgres schema per test session |

---

## Repository layout

```
backend/
  app/
    main.py              app factory, CORS, router mounting, WS route
    config.py            pydantic-settings, reads env
    db.py                async engine, session dependency
    models/              SQLAlchemy models, one module per aggregate
    schemas/             Pydantic request/response models
    api/routes/          auth, users, categories, tasks, todos, tags,
                         sessions, goals, analytics, settings
    core/security.py     hashing, JWT encode/decode
    core/deps.py         get_current_user, get_db
    core/audit.py        write_event() helper
    ws/manager.py        per-user connection registry + broadcast
  alembic/
  tests/
  pyproject.toml
  Dockerfile             multi-stage: builder + slim runtime
frontend/
  src/
    api/                 generated-ish typed client + TanStack Query hooks
    features/            auth, tasks, timer, analytics, calendar, goals
    components/ui/       shadcn primitives
    lib/date.ts          the ONLY place calendar formatting happens
    stores/timer.ts      local tick state, derived from server truth
  Dockerfile             build → nginx static serve
  nginx.conf             serve SPA, proxy /api and /ws
compose.yaml             dev: postgres + api (reload) + vite
compose.prod.yaml        prod: postgres + api (gunicorn/uvicorn workers) + nginx
.env.example
pomodoro.py              legacy CLI, unchanged
```

---

## Data model

Every table carries `user_id`, `created_at`, `updated_at`, `deleted_at` (soft delete).
`archived_at` additionally on categories and tasks. **Every read filters `deleted_at IS NULL`** —
put this in a shared SQLAlchemy query helper rather than repeating the predicate.

```
users            email(citext, unique), password_hash, display_name,
                 timezone default 'Asia/Tehran', calendar_pref 'jalali'|'gregorian',
                 default_work_minutes 25, default_break_minutes 5,
                 long_break_minutes 15, rounds_before_long_break 4,
                 auto_start_breaks, sound_enabled, notifications_enabled

categories       name, color, icon, position, archived_at
                 unique (user_id, lower(name)) where deleted_at is null

tasks            category_id → categories (nullable), title, description,
                 status 'active'|'done', position, archived_at,
                 work_minutes / break_minutes / long_break_minutes /
                 rounds_before_long_break  ← all NULLABLE, null = inherit from user

todos            task_id → tasks, text, done, done_at, position

tags             name, color · task_tags (task_id, tag_id) composite PK

sessions         task_id → tasks (NOT NULL), kind 'work'|'short_break'|'long_break',
                 started_at timestamptz, ended_at timestamptz null,
                 planned_minutes int, duration_seconds int null,
                 status 'running'|'completed'|'cancelled', note text

goals            scope 'daily'|'weekly'|'monthly', target_minutes,
                 category_id nullable, task_id nullable, active

events           entity_type, entity_id, action, payload jsonb   ← task history/log
```

**The one constraint that matters most:**

```sql
CREATE UNIQUE INDEX one_running_session_per_user
  ON sessions (user_id) WHERE status = 'running';
```

This is the database-level replacement for the CLI's fragile "last row with NaN `mins`" rule
(`pomodoro.py:119`). A concurrent second start returns `409`, enforced by Postgres rather than by
application logic — do not also try to guard it with a read-then-write check, which races.

Effort resolution order for any timer: `task.work_minutes` → `user.default_work_minutes`. Resolve it
server-side when a session starts and freeze the answer into `sessions.planned_minutes`, so later
settings changes never retroactively rewrite history.

---

## Timer: server is the source of truth

The DB holds `started_at`; the client only *renders* a countdown derived from it. A refresh, a
closed laptop, or a second device all recover the correct remaining time.

```
POST   /api/sessions/start       {task_id, kind} → 201, or 409 if one is running
GET    /api/sessions/active      → session + server_now
POST   /api/sessions/{id}/complete
POST   /api/sessions/{id}/cancel        (the old `cancel`)
PATCH  /api/sessions/{id}               (the old `fix-end`: set ended_at or duration)
WS     /ws?token=...             → session.started / .completed / .cancelled
```

Two details that are easy to get wrong and expensive to retrofit:

- **Clock skew.** Return `server_now` on `/sessions/active`; the client stores
  `offset = server_now - client_now` and computes every countdown against corrected time. Never
  trust the browser clock for elapsed time.
- **Completion is claimed, not assumed.** When the countdown hits zero the client calls
  `complete`; the endpoint is idempotent and recomputes `duration_seconds` from the timestamps
  server-side. A client that was asleep at zero simply completes late with the correct duration.

The WS manager is an in-memory `dict[user_id, set[WebSocket]]`. That is correct for a single API
container; if the API is ever scaled to multiple replicas this must move to Redis pub/sub — leave a
comment saying so at `ws/manager.py`.

---

## Dates and the calendar toggle

Store UTC `timestamptz`. Convert only at the edges.

The subtle part is aggregation. "Minutes on 1404/06/23" means minutes in *the user's local day*, and
— preserving the original tool's rule — a session belongs to the day it **started** on, even if it
runs past midnight. So bucket in SQL against the user's timezone, never against UTC:

```sql
date_trunc('day', started_at AT TIME ZONE :user_tz)
```

Getting this wrong silently shifts early-morning sessions (local 00:00–03:29 for Tehran) onto the
previous day — the kind of bug that only shows up in the analytics view weeks later. See
`docs/phases/phase-3-analytics.md` for the worked table; the error window is not where intuition
puts it.

Jalali never touches the database. `frontend/src/lib/date.ts` is the single place that formats a
timestamp for display, reading `user.calendar_pref` to pick `date-fns-jalali` or plain `date-fns`.
Funnel every date render through it so the toggle is genuinely global.

---

## Phases

Each phase ends with a running, usable app. **Phases are strictly sequential** — phase N+1 does
not start until N passes its exit gate, and each phase runs
**code → tests → code review → fix** until a review pass comes back clean. The rules are in
[`docs/WORKFLOW.md`](docs/WORKFLOW.md).

Detailed breakdowns, with per-phase todo lists and exit gates:

| | | |
|---|---|---|
| 1 | Foundation, auth, tasks, live timer | [`docs/phases/phase-1-foundation.md`](docs/phases/phase-1-foundation.md) |
| 2 | Todos, tags, per-task settings, history | [`docs/phases/phase-2-structure.md`](docs/phases/phase-2-structure.md) |
| 3 | Analytics, calendar, Jalali toggle | [`docs/phases/phase-3-analytics.md`](docs/phases/phase-3-analytics.md) |
| 4 | Goals, streaks, notifications, production | [`docs/phases/phase-4-goals-production.md`](docs/phases/phase-4-goals-production.md) |

### Phase 1 — Foundation, auth, tasks, live timer
Scaffold both apps and `compose.yaml`; Postgres + Alembic wired up. Users/auth (register, login,
refresh, me) with Argon2 and the `get_current_user` dependency that scopes every subsequent query.
Categories and Tasks CRUD with soft delete and archive. Sessions with the partial unique index and
the WebSocket manager. React: login/register, category sidebar, task list, and the timer view.
Also update `CLAUDE.md` — it currently documents only the CSV CLI and will be actively misleading
once the backend exists.

**Verify:** register → create a category and task → start a timer → hard-refresh mid-run and watch
the countdown resume at the right second → open a second tab and confirm both reflect a completion
→ confirm a second `start` returns 409.

### Phase 2 — Todos, tags, per-task settings, history
Nested todo checklist with drag-ordering, tag CRUD + many-to-many assignment, per-task timer
overrides, and `core/audit.py` writing `events` rows on every create/update/archive/delete/restore.
Task detail page surfaces its own history.

**Verify:** archive a task, restore it, and see both actions in its history; confirm a task-level
work length overrides the user default in a new session's `planned_minutes`.

### Phase 3 — Analytics and calendar
`/api/analytics/{summary,heatmap,by-category,by-task}` and `/api/calendar?month=`, all bucketed with
`AT TIME ZONE`. Dashboard with Recharts (daily bars, category breakdown, year heatmap) and a
month/week calendar of sessions. Ship the Jalali/Gregorian toggle in settings.

**Verify:** log sessions spanning local midnight and confirm each lands on its start day in both
calendar systems; flip the toggle and confirm every date across the app changes.

### Phase 4 — Goals, streaks, notifications, production
Goals CRUD with progress, streak computation against the daily goal, Web Notifications API + sound
on session end (with a permission prompt), then `compose.prod.yaml`: multi-stage builds, nginx
serving the built SPA and proxying `/api` + `/ws`, env-driven secrets, `.env.example`.

**Verify:** `docker compose -f compose.prod.yaml up --build` from a clean volume, register a fresh
user, and complete a full work → break cycle against the production images.

---

## Verification throughout

```bash
docker compose up --build                      # dev, hot reload on both sides
docker compose exec api alembic upgrade head
docker compose exec api pytest                 # backend tests
docker compose exec api pytest tests/test_sessions.py -k concurrent   # single test
```

Backend tests run against a real Postgres service (not SQLite) — the partial unique index, `citext`,
and `AT TIME ZONE` bucketing are all Postgres-specific and would silently pass or fail differently
elsewhere. Minimum coverage per phase: auth scoping (user A cannot read user B's rows), the
one-running-session constraint under concurrent starts, and timezone-boundary aggregation.
