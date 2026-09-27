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

### As built — the third deployment signal

`looks_deployed` started as `cookie_secure or not uses_dev_secret`, and the exit gate's security
review found the hole: those are the two things a rushed deploy forgets *together*. A deployment
that set `CORS_ORIGINS` to its real front end and neither of the others tripped no guard at all and
ran on `dev-only-insecure-secret-change-me` — a secret in this repository, with which anyone can mint
an access token for any user id.

So `_validate` now refuses the dev secret whenever a non-loopback origin is allowed, via a
`has_public_origin` property. Two things about the shape of that fix:

- It is a **dedicated guard, not a third clause on `looks_deployed`.** Adding it there was tried and
  reverted: removing the clause again failed no test, because the dedicated guard runs first and the
  three checks that read `looks_deployed` can therefore never see a public origin as the only
  signal. The clause was unreachable, and an unreachable clause in a security guard is worse than no
  clause — a later reader trusts it.
- It takes no workflow away. A public origin against a local API never worked: the refresh cookie is
  `SameSite=Lax`, so a front end on another site never sends it — the constraint `.env.example`
  already spells out.

Consequently `tests/test_config.py` has two factories: `make()` is a dev config (placeholder secret,
loopback origin) and `deployed()` is a real secret with Secure cookies and a public origin. Origin
*shape* tests pick whichever matches the host they use, since the guards refuse the mismatched
pairings.

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

### As built — two accepted exposures, so they are decisions rather than accidents

**`POST /api/auth/register` answers 409 "An account with that email already exists",** which
re-enables exactly the account enumeration `login()` goes to some trouble to prevent (the dummy
argon2 verify, the single generic 401). Kept, because the alternative is to accept the registration,
send nothing, and report success — which needs email delivery and a verification flow that does not
exist in any phase of this plan, and which silently swallows a real user's typo'd second signup.
Revisit if email verification is ever added.

**The `/ws` access token is written into the uvicorn access log** — `"WebSocket /ws?token=eyJ..."`,
verbatim, on every connect, confirmed at the exit gate. A browser cannot set headers on a WebSocket
handshake, so the token has to travel in the URL; it is an access token (minutes), not the refresh
cookie. This is bounded on a loopback dev box and is *not* bounded once logs are shipped anywhere,
so **phase 4 owns it**: either redact the query string in the production logging config, or move the
token onto `Sec-WebSocket-Protocol` / a single-use ticket, which is a protocol change on both sides
and therefore not a phase-1 edit.

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

### As built

**There is no single-row `GET /api/categories/{id}` or `GET /api/tasks/{id}`,** and that is not an
oversight — neither the list above nor anything in 1.6 asks for one. The frontend reads
`/api/tasks?include_archived=true` once and finds rows in that cache (`lib/listCache.ts`), so a
detail read would be a second source of truth for data already in hand. Hitting one returns **405**,
which looks like a bug when you are walking the API by hand; it is a missing route, not a broken
one. Phase 2's real task-detail page is where to add it if it needs data the list does not carry.

The 404-never-403 rule is covered across *every* route that takes an id, not just a read path — see
the parametrised cases in `test_scoping.py`.

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

Catch `IntegrityError` from the partial index and return 409. *(As built, the 409 body is only
`{"detail": "A session is already running"}`, not the session itself. The client does not need it:
on 409 it refetches `GET /api/sessions/active`, which also brings a fresh `server_now`. Recorded in
1.6 rather than changed, since the frontend works with the API as it stands.)*
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
to the browser, so the 127.0.0.1 spelling makes cross-site calls to `localhost:${API_PORT}` and the
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

### As built

Review loop: twelve `/code-review high` passes returned 10, 10, 9, 10, 10, 9, 10, 9, 8, 8, 9, 10
findings. **It did not reach a clean pass, and 1.6 closes without one** — the strict bar this
section's auth and session logic is meant to hold. Recorded rather than glossed: every finding
was either fixed with a test that fails without the fix (checked by reverting each fix), or is
listed below with the reason it stands. The passes did not spin on the same issues; each went a
layer deeper into interleavings of tabs, clocks, and sign-in/out — and three times found a
regression introduced by an earlier fix (a refresh timeout that abandoned rotations, a
sign-out made "instant" that let a reload undo it, a flag set before login succeeded), each
caught and reversed. The same pattern that led 1.1 to revise the bar for documentation-heavy
diffs; whether 1.6 should be held to strict zero regardless is a call for the phase owner.

Verification: the Definition-of-done flows below were driven in headless Chromium against the
running stack (Playwright — the Chrome extension was unavailable): register → category → task →
start; hard refresh mid-session (−0.2 s drift); second tab updated within ~70–120 ms; double
start (one 201, one 409, both tabs converge on the running session); a browser clock 7 minutes
fast still reads 24:59; socket reconnect across a 1-minute token expiry; overdue-session
prompt; sign-out surviving a reload; 1280 px, 900 px and 390 px layouts. The boxes stay
unticked: the exit gate asks for a pass by hand, which is still to do. `/security-review`: no
findings on the 1.6 diff.

**The two backend gaps 1.6 recorded are now closed** (after 1.6, by the phase owner — 1.6 was
correctly told not to change backend behaviour):

- `POST /sessions/{id}/complete` refuses a session more than `COMPLETE_GRACE` (1 hour) past its
  planned end, with a 409 pointing at `PATCH` or cancel. The window still covers the case the
  countdown's zero-crossing claim actually hits — a throttled or briefly asleep tab — so the normal
  path is untouched. The client's 15-minute policy now sits inside a server-side bound rather than
  being the only thing standing between a forgotten timer and a night of "focus".
- `DELETE /tasks/{id}` and `/archive` refuse while a session runs on that task, as a correlated
  `EXISTS` inside the same `UPDATE` — not a SELECT then a write, which would reproduce the same gap
  in miniature. The client's disabled buttons read one tab's cache; this holds for a second device
  that is seconds behind.

Standing, with reasons:
- *A socket that dies silently* is replaced when the browser reports it is back online, but
  detecting a dead socket in general needs a server ping the backend does not send. Narrowed at the
  exit gate: this now covers only a *network*-dead socket. The case where the **server** gives up on
  a socket is fixed — `broadcast` used to deregister an unresponsive client without closing it, so
  the handler stayed parked in `receive()`, the connection stayed open, and `onclose` — the client's
  only reconnect trigger — never fired. That tab showed a live socket and a frozen timer until its
  access token expired, up to 15 minutes. It is closed with code 1011 now, best-effort.
- *Invalidating `['tasks']` on every session event, and the archived-inclusive task cache,* are
  what this section's plan specifies; at personal scale the extra list download is acceptable.

- **React 19, not 18.** The current shadcn CLI generates React 19 components (`ref` as a plain
  prop, no `forwardRef`). On React 18 every ref through `Input`, `Button asChild` and the Radix
  triggers was silently dropped, which broke focus and menu anchoring. Upgrading React was the
  honest fix; hand-patching generated files would regress on the next `shadcn add`.
- **Check every `shadcn add`.** The CLI wrote `import { cn } from "cn"` into each component and
  installed an unrelated npm package called `cn`. The imports now point at `@/lib/utils` and the
  package is gone; look for this again whenever a component is added.
- **Cross-tab refresh is serialised with the Web Locks API** (`navigator.locks`), on top of the
  in-tab single flight. All tabs share one rotating cookie, so two tabs refreshing together would
  otherwise send the same cookie and the loser would land in the server's grace window with a 401.
- **Clock offset** is `server_now − Date.now()` taken at receipt of each `/sessions/active`
  response and carried on that query's data as `offsetMs` — there is no separate
  `useServerOffset()` hook; a second subscription to the same query only to read one field was
  redundant. Mutation responses carry no `server_now`, so they invalidate that query rather than
  writing to it — every refetch refreshes the offset. The countdown reads the corrected clock *at
  render time*: a stored reading taken before the offset arrived once made a fast browser claim
  `complete` minutes early.
- **Logout is sequenced with refresh.** It waits for any in-flight refresh (whose Set-Cookie is the
  cookie it must revoke) and posts under the same cross-tab lock, so a refresh cannot land after it
  and leave the tab signed in on reload.
- **The socket refreshes once when a handshake is refused**, since the local expiry check reads
  the browser clock and the server reads its own — but at most once per token, so a `/ws` that is
  unreachable for other reasons does not rotate the cookie on every retry.
- **Timer stage lives in the app shell**, not on the task page, so the countdown, the zero-crossing
  `complete` claim and the tab title keep running on every page.
- **One `['tasks']` cache** (`include_archived=true`, filtered on the client), so the timer can
  always name the running task whatever list is on screen.
- **Frontend tests** use Vitest + Testing Library: `docker compose exec web npm test`. They cover
  the single-flight refresh, the countdown (skew, sleep, zero-crossing, retry), the socket's
  reconnect/backoff/token refresh, and category resolution. Each was checked by breaking the
  behaviour and watching the test fail.
- **New frontend dependencies** reach the running container only through `docker compose exec web
  npm install <pkg>` or `up --build --renew-anon-volumes` — see the note in `compose.yaml`.
- **Logout gates refresh until the next sign-in.** If the logout POST fails, a refresh queued
  behind it would still carry a valid cookie; `logout()` sets a flag checked under the refresh
  lock, and only an explicit login/register (`beginSession`) lifts it. Where Web Locks are
  missing, a refresh 401 is retried once after a second: inside `REFRESH_REUSE_GRACE` the server
  refuses a benign duplicate while the sibling's fresh cookie lands. With the lock no two of our
  refreshes can overlap, so there a 401 is final at once and a signed-out load pays no delay.
- **The running task cannot be archived or deleted** from the list: the backend would leave its
  session running against a task no list shows. Finish or discard first.
- **Long-overdue sessions are not claimed silently.** The client auto-claims only within
  `OVERDUE_CLAIM_LIMIT_MS` (15 min) of the end — the throttled or briefly asleep tab the brief
  describes. Beyond that the timer asks: record the planned minutes, record everything, or
  discard. Since 1.6 the server enforces its own, looser bound (`COMPLETE_GRACE`, 1 hour), so the
  client policy is now defence in depth rather than the only guard — and both "record" choices go
  through fix-end (`duration_minutes`), never `/complete`, which refuses a session that far past
  its end. "Record everything" sends the whole span, measured on the server's clock at the click.
  A delete or archive refused with the new 409 re-reads the active session, so a tab that had not
  yet heard of a session started elsewhere shows it.
- **The running task cannot be removed, now on both sides.** The list disables Archive, Delete
  and Done on the running task; since 1.6 the API also returns 409 from `DELETE /tasks/{id}` and
  `/archive` while a session runs, so a second device a few seconds behind cannot leave a session
  running against a task no list shows.
- **Logout is single-flight, and sign-in waits for any logout or refresh in flight** — either one
  landing after a new login would overwrite it (a logout clears its token and deletes the cookie
  it just set; the boot refresh installs its own token). A sign-in also supersedes the boot
  restore — once the login has succeeded, so a wrong password keeps the session the boot is
  still loading. Logout's waits share one 20 s deadline (below). A refresh gets a much longer one,
  60 s: abandoning a refresh the server still completes rotates the cookie out from under the
  browser, so it must never be cut short — but one that never answers would hold the cross-tab
  lock and every tab's boot behind it. A minute is far past any real refresh, and turns a hung
  server into "can't reach the server — try again" instead of an endless loading mark. If the logout never reaches the server the
  user is told this browser may still be signed in, rather than shown a clean sign-out.
- **A refresh that returns a different account switches the tab over cleanly.** All tabs share
  one refresh cookie, so signing in as someone else in one tab makes every other tab's next
  refresh return that user. The client compares it with the tab's user; on a change the request
  that triggered the refresh is refused rather than replayed under the new account, the cache is
  cleared, and the signed-in tree remounts (keyed on user id), socket included.
- **The zero-crossing claim re-reads `/sessions/active` before completing.** Zero by the cached
  clock offset is not zero by the server if the browser clock jumped since (NTP correcting after
  sleep); the fresh `server_now` decides, and refreshes the offset the countdown uses.
- **The boot restore takes the user from the refresh response**, which already carries it — no
  `/auth/me` round trip, and the tab knows whose it is from its first refresh.
- **A failed background refetch keeps the list it already has**, with a note, rather than hiding
  it behind an error.
- **Every request remembers the session it was sent under.** If a sign-in, a sign-out, or a
  refresh that found another account has happened by the time its 401 comes back, it is refused
  rather than retried — reusing a newer token from memory would otherwise replay it as someone
  else. Sign-in itself runs under the cross-tab refresh lock, after any refresh in flight, so the
  previous account's rotated cookie cannot land over the new one.
- **An auth epoch orders sign-in, sign-out and refresh.** Every sign-in and sign-out bumps it, and
  a refresh landing under an older epoch is discarded whole — it cannot install its token over a
  new sign-in, nor report a lapsed session while the user signs out on purpose. With that, logout
  no longer has to wait out a refresh indefinitely: everything it waits on (a refresh in flight,
  the lock, the POST) shares one 20 s deadline, and sign-in waits only for logout.
- Accepted: each socket open re-fetches the active session and session lists, which on first
  page load duplicates the mount's fetch (and repeats at each token expiry). Skipping it risks
  missing an event published between the fetch and the socket opening; one small request per
  open is the cheaper side.
- **Signing out shows "Signing out…" until the server confirms**, capped at 20 s. The token is
  dropped at once, but the login page is not shown until the cookie is revoked: showing it
  earlier invited an immediate reload, which aborted the logout POST and signed straight back in
  (caught in the browser, not by a test — a pass-8 fix had made sign-out "instant"). Past the
  cap it signs out anyway, with the may-still-be-signed-in warning. It also no longer carries the
  old page into the next sign-in, which may be someone else's; a lapsed session or a deep link
  still returns to where it was. The redirect guard also refuses `/\host`.
- **Invalidation keeps TanStack's default `cancelRefetch`.** Joining an in-flight refetch after a
  mutation can cache an answer from before the change; a duplicate request is the cheaper cost.
  A failed end-session claim rechecks only the active session, not the task list.
- **Settling an overdue session re-reads `/sessions/active` first.** Fix-end, unlike complete,
  rewrites a completed session, and the overdue screen can be stale after sleep.
- **"Record all" on an overdue session is offered only up to 24 hours**, the `MAX_SESSION_MINUTES`
  that fix-end — which it goes through — enforces.
- **A running session whose task this tab has never loaded** (started elsewhere while this tab's
  socket was down) triggers one refetch of the task list, so the timer can name it.
- Checked and not changed: dialogs opened from dropdown items (Edit, Delete) — focus lands in the
  dialog and `body` pointer-events are restored on close with the Radix version installed.
- **No break follow-up.** A "Focus done — take a break?" prompt was built and then removed in
  review: work → break cycling is phase 4.3's, and a manual version of it here would be that work
  pulled forward. Phase 1 runs one session at a time; breaks can be started through the API only.
- Not built here, deliberately: drag-to-reorder of tasks (the API exists; no 1.6 item asks for the
  UI) and editing settings (the Settings page is a read-only stub). The Task detail page is a stub
  listing the task's sessions; phase 2 builds the real one.

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

### As built

The fixtures and all four files landed alongside the code in 1.2–1.5 rather than as a batch here,
which is what the workflow's "tests alongside the code" rule asks for. What 1.8 actually did was
reconcile the table above against the suite. One row was not honestly ticked: every start test was
*sequential*, so an application-level "is anything running?" pre-check would have passed them all —
the overlap the partial index exists for was untested.
`test_sessions.py::test_two_concurrent_starts_yield_one_201_and_one_409` closes it, staging the
interleaving across two `AsyncSession`s and calling `start_session` directly. Verified three ways:
it fails with the index dropped, and — with the index dropped *and* a pre-check injected — it fails
while the sequential test passes, which is the distinction it exists to draw.

`conftest.py` grew a third client fixture the plan did not anticipate, `real_db_client`: a session
and connection per request, real commits, `TRUNCATE users CASCADE` on teardown. The `client` fixture
shares one session so the outer transaction can roll everything back, and that makes genuine
concurrency untestable — `SELECT … FOR UPDATE` never blocks against its own transaction, and
`asyncio.gather` on a shared `AsyncSession` raises rather than running in parallel.

254 tests.

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
- [x] Migration 0004 — `sessions` + `one_running_session_per_user` partial index
- [x] start (409 via `IntegrityError`, no pre-check SELECT) / active / complete / cancel / fix-end
- [x] Freeze `planned_minutes` at start from `user.default_work_minutes` (the per-task override is phase 2)
- [x] `ws/manager.py` + `/ws` route with query-token auth
- [x] Broadcast started / completed / cancelled

**Frontend**
- [x] `api/client.ts` with single-flight 401 → refresh → retry
- [x] `AuthProvider`, `ProtectedRoute`, Login + Register pages
- [x] App shell: category sidebar + task list
- [x] Server clock offset (on `useActiveSession`'s data — see *As built*), `TimerRing` (recompute, never decrement)
- [x] `useSessionSocket` with reconnect backoff
- [x] Remaining time in `document.title`
- [x] Vitest suite for the client, timer, socket and category logic

**Tests & docs**
- [x] `conftest.py` against real Postgres, rollback per test
- [x] `test_auth.py`, `test_scoping.py`, `test_sessions.py`, `test_tasks.py`
- [x] Rewrite `CLAUDE.md` for the new stack

---

## Exit gate

Phase 1 is not finished until every item in the gate checklist in
[`docs/WORKFLOW.md`](../WORKFLOW.md#exit-gate) passes:
todos ticked · `pytest` fully green · `/code-review high` clean over the phase diff ·
Definition of done verified by hand in a browser · a clean-volume rebuild
(`docker compose down -v && docker compose up --build`) walked end to end · committed and merged.

`/security-review` is **mandatory** for this phase — it introduces auth and the `scoped()` ownership helper that every later phase trusts.

**Phase 2 does not begin until this gate passes.**
