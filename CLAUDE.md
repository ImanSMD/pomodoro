# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this
repository.

## What this is

A pomodoro tracker: FastAPI + Postgres backend, React + Vite frontend, Docker Compose for both.
It grew out of `pomodoro.py`, a single-file CSV CLI that is still in the repo as the retired
original (see *Legacy CLI* at the bottom) — the app starts from an empty database and imports
nothing from it.

The design lives in [`PLAN.md`](PLAN.md), split into four phase documents under `docs/phases/`.
**Read the relevant phase document before writing any code for it** — the phase docs carry the
decisions and the *As built* notes explaining where the code deviates from the plan and why.

**Two rules, from [`docs/WORKFLOW.md`](docs/WORKFLOW.md) — follow them:**

1. **Phases are strictly sequential.** Do not start phase N+1 until phase N has passed its exit
   gate. Do not pull work forward from a later phase or section.
2. **Every section runs the loop: code → tests → code review → fix, repeating until no finding
   above `low` remains and `pytest` is fully green.** Sections carrying real logic — auth,
   ownership scoping, session state, analytics bucketing — hold to strict zero findings. Tests are
   written alongside the code, not batched at the end. Use `/code-review high`, and
   `/security-review` for phases touching auth, scoping, or deployment.

A phase closes only when its exit-gate checklist passes in full, including a clean-volume rebuild
and hand-verification in a browser. Keep the phase documents' todo lists ticked as work lands, and
if a plan turns out to be wrong, update the document rather than silently deviating.

## Commands

Everything runs in compose; there is no host-side virtualenv or `node_modules`.

```bash
docker compose up --build                  # db + api (reload) + web (vite)
docker compose up --build --renew-anon-volumes   # after changing frontend deps — see compose.yaml
docker compose down -v                     # drop the volume too (see the test-database note below)

docker compose exec api pytest             # backend suite
docker compose exec api pytest tests/test_sessions.py -k concurrent   # one test
docker compose exec api alembic revision --autogenerate -m "..."
docker compose exec api alembic upgrade head
docker compose exec api alembic check      # must report no drift before committing a model change

docker compose exec web npm test           # vitest
docker compose exec web npm run typecheck
docker compose exec web npm install <pkg>  # writes package.json back to the host checkout
```

Host ports are configurable because this machine already has things bound: `API_PORT`, `WEB_PORT`,
`DB_PORT` in `.env` (defaults 8000 / 5173 / 5432). The container-side ports never change, so
`DATABASE_URL` and the compose hostnames are unaffected. `VITE_API_URL` is derived from `API_PORT`
by compose — **do not set it in `.env`**, or the browser will keep calling the old port.

The test database is created by `db/init/01-create-test-db.sh`, which Postgres runs **only on a
first-time volume init**. If the suite reports the database missing, the `pgdata` volume predates
that script: `docker compose down -v` and bring it back up.

## Architecture

```
backend/app/
  main.py          app factory (create_app(settings)), CORS, routers, /ws, lifespan engine probe
  config.py        Settings — every environment guard lives here, see below
  db.py            async engine + DbSession dependency (takes HTTPConnection, so /ws gets it too)
  models/          SQLAlchemy 2.0 declarative; base.py has the UUID pk / timestamps / soft-delete mixins
  schemas/         Pydantic v2 request + response models, and the session limit constants
  core/            security.py (argon2 + JWT) · deps.py (auth, scoped, get_owned_or_404)
                   mutations.py (apply_once) · cookies.py (refresh cookie + RefreshTokenInvalid)
  api/routes/      auth · settings · categories · tasks · sessions
  ws/manager.py    in-memory dict[user_id, set[WebSocket]]
alembic/versions/  0001 citext+users · 0002 refresh_tokens · 0003 categories+tasks · 0004 sessions

frontend/src/
  api/client.ts    fetch wrapper: NetworkError, single-flight 401 → refresh → retry, cross-tab lock
  api/types.ts     hand-written mirrors of the response schemas
  app/             AppShell, Navigation, Page
  features/        auth · categories · tasks · timer · realtime · settings
  lib/dates.ts     the ONLY place a timestamp is formatted for display
  lib/listCache.ts TanStack Query cache-update helpers shared by the mutations
```

### Invariants that are easy to break

**Every read goes through `scoped()`.** `core/deps.py` has one helper —
`scoped(stmt, model, user)` adds `user_id == user.id AND deleted_at IS NULL`. Do not hand-write
either predicate in a route; a missed `deleted_at` resurrects soft-deleted rows and a missed
`user_id` is a cross-tenant leak. `get_owned_or_404` is the single-row form and raises **404, never
403** — a 403 confirms the row exists to someone who does not own it.

**Conditional writes decide the condition in the database.** `core/mutations.py:apply_once` issues
`UPDATE … WHERE id = :id AND <condition>` and returns whether it applied; the route turns `False`
into a 409. Never read the row, check its state in Python, then write — two clicks interleave
through that window. The same rule is why `POST /sessions/start` has no "is anything running?"
`SELECT`: the partial unique index `one_running_session_per_user` is the check, and the route maps
its `IntegrityError` to a 409, matching on the constraint **name** so an unrelated constraint is not
misreported. `tests/test_sessions.py::test_two_concurrent_starts_yield_one_201_and_one_409` fails if
that guard is replaced by a pre-check.

**Timestamps are UTC `timestamptz` everywhere.** Jalali never reaches the database. The calendar is
display only, and `frontend/src/lib/dates.ts` is the single place it happens — via `Intl` with the
`en-u-ca-persian` calendar rather than the `date-fns-jalali` the plan named. A session belongs to
the local day it **started** on, so any future aggregation buckets on
`date_trunc('day', started_at AT TIME ZONE :user_tz)` and never on UTC.

**The server owns the timer.** The database holds `started_at`; the client only renders a countdown
derived from it. `GET /api/sessions/active` returns `server_now` so the client can correct a skewed
browser clock, `planned_minutes` is frozen at start so changing a default later cannot rewrite
history, and completion is *claimed* by the client — `/complete` recomputes the duration from the
timestamps and refuses a session more than `COMPLETE_GRACE` (1 h) past its planned end, so a timer
forgotten overnight cannot record the night as focus.

**`config.py` refuses to start on a dangerous configuration** rather than warning. It pairs
settings: a dev `JWT_SECRET` with `COOKIE_SECURE`, a real secret *without* `COOKIE_SECURE`, a
credentialed CORS wildcard, and a loopback origin on anything that `looks_deployed`. Origins are
shape-validated and normalised because Starlette compares them to the `Origin` header as exact
strings. Add new environment reads here, not in `main.py`.

**The WS manager is per-process.** Correct for one API container only; scaling to replicas means
Redis pub/sub. There is a comment saying so at the top of `ws/manager.py`.

### Auth, briefly

Argon2 password hashes, a short-lived JWT access token in memory, and a refresh token in an
httpOnly `SameSite=Lax` cookie scoped to `/api/auth`. Refresh rotates on every use; replaying a
spent token revokes the whole family, except within `REFRESH_REUSE_GRACE` (10 s) where it is
treated as a duplicate — two tabs restoring at once must not log the user out. `login` runs a dummy
argon2 verify for an unknown address so the endpoint cannot be timed to enumerate accounts. Only the
hash of a refresh token is stored.

The cookie is `localhost`-only by design: to a browser `127.0.0.1` and `localhost` are different
sites, so mixing the spellings silently withholds the refresh cookie. Use `localhost` in the
browser.

## Tests

pytest against a **real Postgres** — the partial index, `citext` and `AT TIME ZONE` bucketing are
all Postgres-specific and would pass or fail differently on SQLite. Three client fixtures, and
picking the wrong one is the usual way a test ends up proving nothing:

| fixture | what it is | use it for |
|---|---|---|
| `client` | one shared `AsyncSession` inside an outer transaction rolled back on teardown | almost everything |
| `authed_client` | `client` with a registered user's bearer token | almost everything |
| `real_db_client` | a **session per request**, real commits, `TRUNCATE users CASCADE` on teardown | anything about concurrency, locking or commit visibility |

`asyncio.gather` over the in-process ASGI transport does **not** reliably interleave requests, and
two requests on `client` share one transaction so they can never conflict. A concurrency test
therefore stages the interleaving explicitly across two `AsyncSession`s and calls the production
helper or route function — see `test_tasks.py::test_apply_once_is_decided_in_the_database_not_by_a_pre_check`
and `test_sessions.py::test_two_concurrent_starts_yield_one_201_and_one_409` for the pattern.

**Verify a test by breaking the code.** Remove the guard, watch that specific test fail, put it
back. Several tests in this suite passed against the bug they were written for until this was done
routinely; a test nobody has seen fail is not yet evidence.

Frontend: vitest + Testing Library under `frontend/src`, colocated as `*.test.ts(x)`.

## Legacy CLI

`pomodoro.py` is the retired original: a single-file argparse CLI that logged sessions to
`df_pomodoro.csv` with Jalali dates packed as `YYYYMMDD` integers, treating "a task is running" as
*the last row whose `mins` is NaN*. It still runs against its own CSV and is kept as reference. It
is **not** being migrated and its data is not imported.

```bash
POMODORO_DIR=/tmp/pomo-test python3 pomodoro.py start test   # always set POMODORO_DIR when testing,
POMODORO_DIR=/tmp/pomo-test python3 pomodoro.py end          # or it writes the real log beside the script
```

Do not change it as part of app work.
