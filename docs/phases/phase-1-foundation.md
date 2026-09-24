# Phase 1 — Foundation, Auth, Tasks, Live Timer

**Outcome:** `docker compose up` gives you a working app. You can register, log in, create
categories and tasks, and run a real pomodoro timer that survives a hard refresh and stays in sync
across two browser tabs.

This phase builds the spine. Everything in Phases 2–4 hangs off the decisions made here — the
auth scoping dependency, the soft-delete query helper, and the running-session constraint are all
things that are painful to retrofit, so they get done properly now.

> **Workflow:** This is the first phase; nothing precedes it. Work through the sections in order, running
> **code → tests → code review → fix** on each one until a review pass comes back clean.
> See [`docs/WORKFLOW.md`](../WORKFLOW.md) for the full rules.

---

## 1.1 Scaffold and tooling

**Backend deps** (`backend/pyproject.toml`):
`fastapi`, `uvicorn[standard]`, `sqlalchemy[asyncio]`, `asyncpg`, `alembic`, `pydantic-settings`,
`passlib[argon2]`, `pyjwt`, `python-multipart` · dev: `pytest`, `pytest-asyncio`, `httpx`

**Frontend:** `npm create vite@latest frontend -- --template react-ts`, then Tailwind, `shadcn init`,
`@tanstack/react-query`, `react-router-dom`, `date-fns`.

**`compose.yaml`** (dev) — three services:

| service | image / build | notes |
|---|---|---|
| `db` | `postgres:16-alpine` | named volume, `healthcheck: pg_isready` |
| `api` | `./backend` | `uvicorn app.main:app --reload --host 0.0.0.0`, source bind-mounted, `depends_on: db: condition: service_healthy` |
| `web` | `./frontend` | `npm run dev -- --host`, source bind-mounted, node_modules in an anonymous volume |

**`.env.example`:** `POSTGRES_USER/PASSWORD/DB`, `DATABASE_URL`, `JWT_SECRET`,
`ACCESS_TOKEN_TTL_MINUTES=15`, `REFRESH_TOKEN_TTL_DAYS=30`, `CORS_ORIGINS`, `VITE_API_URL`.

Extend `.gitignore` with `.env`, `node_modules/`, `.venv/`. The existing CSV ignores stay — the
legacy CLI still runs.

---

## 1.2 Database foundation

- `app/config.py` — `Settings(BaseSettings)` reading the env above. Two guards carried over
  from the 1.1 review, both of which need `Settings` to exist:
  - **Refuse to boot** when `JWT_SECRET` is still the dev placeholder and `COOKIE_SECURE` is true.
    Compose ships the placeholder as a default so a fresh clone runs, which means without this
    guard a production-shaped deploy would sign 30-day refresh tokens with a value in the repo.
  - **Warn when `CORS_ORIGINS` resolves empty** — an empty allowlist rejects every cross-origin
    request silently, with no log line to explain it.
  - **Pair the empty-`CORS_ORIGINS` guard with the `JWT_SECRET` check.** Today it refuses only
    when `COOKIE_SECURE` is true, but compose ships `COOKIE_SECURE=false`, so a TLS-terminated
    deploy that sets neither variable boots with `allow_origins=["http://localhost:5173"]` and
    credentials on — a page on the victim's own localhost keeps credentialed access to production.
    Once `Settings` knows whether `JWT_SECRET` is still the dev placeholder, refuse the localhost
    fallback whenever it is not.
  - **Refuse `CORS_ORIGINS=*` while credentials are enabled.** Starlette echoes the caller's
    `Origin` back rather than sending a literal `*`, so with the refresh cookie and bearer token on
    these requests, any website gets a credentialed read of the API. `main.py` already raises at
    boot; keep that behaviour when the check moves onto `Settings`, and cover it with a test —
    phase 4 drives production CORS from this same variable.
- Move `main.py`'s direct `os.getenv("CORS_ORIGINS")` read onto `Settings`.
- `app/db.py` — `create_async_engine`, `async_sessionmaker(expire_on_commit=False)`, `get_db` dep.
- `app/models/base.py` — `Base(DeclarativeBase)` plus mixins used by nearly every table:
  - `UUIDPrimaryKey` — `id: Mapped[UUID] = mapped_column(default=uuid4, primary_key=True)`
  - `Timestamps` — `created_at`, `updated_at` (`server_default=func.now()`, `onupdate`)
  - `SoftDelete` — `deleted_at: Mapped[datetime | None]`
- Alembic `env.py` pointed at the async engine and `Base.metadata`.

**Migration 0001:** `CREATE EXTENSION IF NOT EXISTS citext;` + `users`.

```
users   id, email CITEXT UNIQUE NOT NULL, password_hash, display_name,
        timezone TEXT NOT NULL DEFAULT 'Asia/Tehran',
        calendar_pref TEXT NOT NULL DEFAULT 'jalali',
        default_work_minutes 25, default_break_minutes 5,
        long_break_minutes 15, rounds_before_long_break 4,
        auto_start_breaks BOOL DEFAULT false,
        sound_enabled BOOL DEFAULT true, notifications_enabled BOOL DEFAULT true,
        created_at, updated_at
```

---

## 1.3 Auth

**Migration 0002** — `refresh_tokens (id, user_id → users ON DELETE CASCADE, token_hash unique,
expires_at, revoked_at)`. Rotation needs server-side state: without a record of what was issued
there is no way to invalidate the previous token when a new one is handed out. Only the SHA-256 of
the token is stored, so a leaked row cannot be replayed. (This was not in the original plan, which
numbered categories/tasks as 0002 — those are now 0003.)

`app/core/security.py` — argon2 `hash_password` / `verify_password`, `create_access_token`,
`create_refresh_token`, `decode_token`.

`app/core/deps.py` — `get_current_user` reading the `Authorization: Bearer` header;
export `CurrentUser = Annotated[User, Depends(get_current_user)]` so every route reads cleanly.

```
POST /api/auth/register   {email, password, display_name} → 201 {user, access_token} + refresh cookie
POST /api/auth/login      {email, password}               → 200 {user, access_token} + refresh cookie
POST /api/auth/refresh    (cookie)                        → {access_token}   ← rotates the refresh token
POST /api/auth/logout                                     → clears cookie
GET  /api/auth/me                                         → user
GET  /api/settings  ·  PATCH /api/settings                → timezone, calendar_pref, durations, toggles
```

**Token handling.** Refresh token in an `httpOnly`, `SameSite=Lax`, `Secure`-in-prod cookie; access
token held in React memory only.

**Access tokens are not revocable, deliberately.** Logout and reuse detection end the refresh
chain, but an access token already issued stays valid until it expires — so a thief who has just
exchanged a stolen cookie keeps API access for up to `ACCESS_TOKEN_TTL_MINUTES`. That 15-minute
window *is* the reason the TTL is short, and closing it properly means a `tokens_valid_after`
column on `users` compared against the token's `iat` on every request. `get_current_user` already
loads the user, so the cost is a column rather than a query — worth doing if the TTL ever grows,
and not worth a migration at 15 minutes. The comments in `auth.py` must not claim otherwise.

**Reuse detection has a grace window.** A replay within `REFRESH_REUSE_GRACE` of the rotation is
treated as a duplicate rather than a theft. Two tabs restoring at once, React StrictMode, or a
proxy retrying a timed-out POST all send the cookie a sibling request just spent; revoking the
family there would kill the successor that sibling issued, leaving the browser holding a dead
cookie and the user hard-logged-out for doing nothing wrong. Do not put either in `localStorage` — anything that lands there is
readable by any XSS on the page, and a 30-day refresh token is the worst possible thing to leak.
Rotate the refresh token on every use.

Login failures return a single generic 401 for both "no such email" and "wrong password", so the
endpoint can't be used to enumerate which emails are registered.

---

## 1.4 Categories and Tasks

**Migration 0003** — `categories` and `tasks` per the schema in `PLAN.md`, including
`CREATE UNIQUE INDEX ... ON categories (user_id, lower(name)) WHERE deleted_at IS NULL`.

**The scoping helper is the most important piece of this section.** Write it once:

```python
# app/core/deps.py
def scoped(stmt, model, user: User):
    return stmt.where(model.user_id == user.id, model.deleted_at.is_(None))
```

Every single read goes through it. The moment one endpoint forgets `user_id ==`, one user can read
another's data — so make this the only way queries are built, and cover it with the cross-user test
in 1.8 rather than relying on discipline.

```
GET    /api/categories?include_archived=false
POST   /api/categories                 {name, color, icon}
PATCH  /api/categories/{id}
DELETE /api/categories/{id}            → soft delete (sets deleted_at)
POST   /api/categories/{id}/archive  ·  /restore

GET    /api/tasks?category_id=&status=&q=&include_archived=
POST   /api/tasks                      {title, description, category_id}
PATCH  /api/tasks/{id}   ·  DELETE /api/tasks/{id}
POST   /api/tasks/{id}/archive  ·  /restore  ·  /complete
PATCH  /api/tasks/reorder              {ids: [...]}  → rewrites position
```

A missing or other-user row returns **404, never 403** — a 403 confirms the id exists.

---

## 1.5 Sessions and the WebSocket

**Migration 0004** — `sessions`, plus the constraint this whole design leans on:

```python
op.execute("""
    CREATE UNIQUE INDEX one_running_session_per_user
      ON sessions (user_id) WHERE status = 'running'
""")
```

```
POST  /api/sessions/start          {task_id, kind}  → 201 · 409 if one already runs
GET   /api/sessions/active                          → {session, server_now} | null
POST  /api/sessions/{id}/complete                   → idempotent
POST  /api/sessions/{id}/cancel
PATCH /api/sessions/{id}                            → fix-end: explicit ended_at or duration
GET   /api/sessions?task_id=&limit=                 → history list
WS    /ws?token=<access>
```

On `start`, take the effort from `user.default_work_minutes` and **freeze it** into
`sessions.planned_minutes`. Later settings changes must never retroactively rewrite what a past
session was.

Note there is no per-task override to consult yet: `tasks.work_minutes` and its siblings arrive with
phase 2's migration 0007, so resolving `task.work_minutes ?? user.default_work_minutes` here would
mean pulling that work forward. Phase 2 adds the fallback to the same frozen value, in
`core/settings.py`.

Catch `IntegrityError` from the partial index and return 409 carrying the already-running session.
Do not pre-check with a `SELECT` — two rapid clicks interleave between the read and the write, and
the index is what actually makes this safe.

`app/ws/manager.py` — `dict[user_id, set[WebSocket]]` with `connect` / `disconnect` /
`broadcast(user_id, event)`. Broadcast `session.started`, `session.completed`, `session.cancelled`.
The WS authenticates by query-string token because browsers cannot set headers on a WebSocket
handshake. Leave a comment: **in-memory state is correct for one API container only — multiple
replicas need Redis pub/sub.**

---

## 1.6 Frontend

**Category name resolution.** A task may reference an archived category — archiving is reversible
and keeping the grouping is its entire purpose, so unlike delete (which detaches) the link survives.
The sidebar must therefore load `GET /api/categories?include_archived=true`, render archived ones
muted, and offer only live ones in the picker. Loading the default list alone leaves the client with
`category_id`s it cannot name.

**Open the app at `http://localhost:5173`, not `http://127.0.0.1:5173`.** They are different sites
to the browser, so the 127.0.0.1 spelling makes cross-site calls to `localhost:8000` and the
`SameSite=Lax` refresh cookie is withheld — login works, then dies at the first token refresh.
CORS allows only the `localhost` origin so this fails loudly instead.

- `api/client.ts` — fetch wrapper attaching the access token; on 401 it calls `/auth/refresh` once,
  retries the original request, and redirects to login if that fails. Single-flight the refresh so
  ten parallel 401s don't fire ten refreshes.
- `AuthProvider` + `ProtectedRoute`; pages: Login, Register, app shell (category sidebar + task
  list), Task detail (stub), Settings (stub).
- `features/timer/`:
  - `useServerOffset()` — `server_now - Date.now()`, stored once per active-session fetch.
  - `useActiveSession()` — TanStack Query on `['session','active']`.
  - `TimerRing` — 1s interval recomputing remaining from `started_at + planned_minutes` **using the
    corrected clock**, never from a locally decremented counter (a decrementing counter drifts and
    freezes when the tab is backgrounded).
  - On reaching zero the client calls `complete`. The endpoint recomputes duration from timestamps,
    so a tab that was asleep at zero still records the right numbers.
- `useSessionSocket()` — opens the WS, invalidates `['session','active']` and `['tasks']` on each
  event, and reconnects with backoff.
- Mirror the remaining time into `document.title` so the countdown is visible in a background tab.

---

## 1.7 Update CLAUDE.md

The current file documents only the CSV CLI and becomes actively misleading the moment the backend
exists. Rewrite it for the new architecture; keep a short section noting `pomodoro.py` is the
retired original, still runnable, reading its own CSV.

---

## 1.8 Tests

`tests/conftest.py` — a real Postgres (the partial index and `citext` do not exist in SQLite), each
test in a transaction that rolls back, plus a `client` fixture with a registered-and-logged-in user.

The test database is created by `db/init/01-create-test-db.sh`, which Postgres runs **only on a
first-time volume init**. A `pgdata` volume from an earlier `up` will not have it — if the suite
reports the database missing, `docker compose down -v` and bring it back up.

| file | covers |
|---|---|
| `test_auth.py` | register · duplicate email → 409 · bad password → 401 · `/me` requires a token · refresh rotates |
| `test_scoping.py` | user A gets **404** for user B's category, task, and session |
| `test_sessions.py` | two concurrent starts → exactly one 201 and one 409 · complete computes duration · complete is idempotent · cancel · fix-end |
| `test_tasks.py` | soft delete hides from list · archive/restore · reorder persists |

---

## Definition of done

- [ ] `docker compose up --build` brings up db + api + web from a clean volume
- [ ] `alembic upgrade head` applies 0001–0004 cleanly
- [ ] Register → create category → create task → start timer
- [ ] **Hard-refresh mid-session:** countdown resumes at the correct second
- [ ] **Second tab:** completing in one updates the other within a second
- [ ] **Double-start:** the second returns 409 and the UI shows the running session
- [ ] `pytest` green, including the concurrent-start and cross-user tests
- [ ] `CLAUDE.md` describes the new stack

---

## Todo

**Scaffold**
- [x] `backend/pyproject.toml` + app package skeleton
- [x] `npm create vite` frontend, Tailwind, shadcn init, TanStack Query, React Router
- [x] `compose.yaml` with db / api / web + healthcheck-gated `depends_on`
- [x] `.env.example`, extend `.gitignore`

**Database**
- [x] `config.py` settings, `db.py` async engine + `get_db`
- [x] `models/base.py` mixins (UUID pk, timestamps, soft delete)
- [x] Alembic wired to the async engine
- [x] Migration 0001 — citext + `users`

**Auth**
- [x] Migration 0002 — `refresh_tokens`
- [x] `core/security.py` — argon2 + JWT encode/decode
- [x] `core/deps.py` — `get_current_user`, `CurrentUser`
- [x] register / login / refresh / logout / me
- [x] refresh-token cookie (httpOnly, SameSite=Lax, rotate on use)
- [x] `GET`/`PATCH /api/settings`

**Categories & Tasks**
- [x] Migration 0003 — `categories`, `tasks`, partial unique name index
- [x] `scoped()` helper — the single path all reads go through
- [x] Categories CRUD + archive/restore
- [x] Tasks CRUD + archive/restore/complete + reorder
- [x] 404 (not 403) on missing-or-other-user rows

**Sessions & WebSocket**
- [ ] Migration 0004 — `sessions` + `one_running_session_per_user` partial index
- [ ] start (409 via `IntegrityError`, no pre-check SELECT) / active / complete / cancel / fix-end
- [ ] Freeze `planned_minutes` at start from `user.default_work_minutes` (the per-task override is phase 2)
- [ ] `ws/manager.py` + `/ws` route with query-token auth
- [ ] Broadcast started / completed / cancelled

**Frontend**
- [ ] `api/client.ts` with single-flight 401 → refresh → retry
- [ ] `AuthProvider`, `ProtectedRoute`, Login + Register pages
- [ ] App shell: category sidebar + task list
- [ ] `useServerOffset`, `useActiveSession`, `TimerRing` (recompute, never decrement)
- [ ] `useSessionSocket` with reconnect backoff
- [ ] Remaining time in `document.title`

**Tests & docs**
- [ ] `conftest.py` against real Postgres, rollback per test
- [ ] `test_auth.py`, `test_scoping.py`, `test_sessions.py`, `test_tasks.py`
- [ ] Rewrite `CLAUDE.md` for the new stack

---

## Exit gate

Phase 1 is not finished until every item in the gate checklist in
[`docs/WORKFLOW.md`](../WORKFLOW.md#exit-gate) passes:
todos ticked · `pytest` fully green · `/code-review high` clean over the phase diff ·
Definition of done verified by hand in a browser · a clean-volume rebuild
(`docker compose down -v && docker compose up --build`) walked end to end · committed and merged.

`/security-review` is **mandatory** for this phase — it introduces auth and the `scoped()` ownership helper that every later phase trusts.

**Phase 2 does not begin until this gate passes.**
