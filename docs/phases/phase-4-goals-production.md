# Phase 4 — Goals, Streaks, Notifications, Production

**Outcome:** the app closes the loop — targets to hit, streaks to keep, a timer that cycles into
breaks and tells you when it's done — and it runs behind nginx from production images.

> **Workflow:** **Do not start this phase until Phase 3 has passed its exit gate.** Work through the sections in order, running
> **code → tests → code review → fix** on each one until a review pass comes back clean.
> See [`docs/WORKFLOW.md`](../WORKFLOW.md) for the full rules.

---

## 4.1 Goals

**Migration 0010** — `goals (id, user_id, scope 'daily'|'weekly'|'monthly', target_minutes,
category_id NULL, task_id NULL, active, …)`.

A goal scoped to a category or task counts only sessions under it; both null means all focus time.

```
GET/POST/PATCH/DELETE  /api/goals
GET /api/goals/progress?date=      → [{goal, achieved_minutes, target_minutes,
                                       pct, met, period_start, period_end}]
```

Period boundaries follow the **user's calendar preference**, and this is where it gets sharp: a
"weekly" goal in Jalali mode runs Saturday→Friday, in Gregorian mode Monday→Sunday, and a "monthly"
goal tracks Jalali months — which are 31, 30, or 29 days and do not line up with Gregorian ones.
Resolve period bounds through one server-side helper that takes `(scope, date, calendar_pref, tz)`
and returns a UTC range. Reuse the Phase 3 `local_day` bucketing underneath.

Python side needs `jdatetime` for this — the same dependency the original CLI used (`pomodoro.py:48`).

---

## 4.2 Streaks

```
GET /api/analytics/streaks → {current, longest, last_active_day, days: [{day, met}]}
```

A day counts when its completed minutes meet the active daily goal; with no daily goal set, fall
back to "any completed session". Compute in SQL over distinct local days — pull the day list, walk
it backwards in Python. Do not store a counter: a cached streak that drifts out of sync with the
sessions is a bug you cannot see, and this query is cheap with the Phase 3 indexes.

**Today must not break the streak until the day is over.** Anchor the walk at the user's local
today, and treat it as neutral rather than as a miss — otherwise every user's streak reads as broken
every morning.

---

## 4.3 Auto-cycling and notifications

The Phase 1 timer runs one session at a time. Now it cycles: work → short break → work → … →
long break every `rounds_before_long_break`, using the task's effective settings from Phase 2.

Round position derives from completed work sessions on that task in the current local day, so a
refresh can't lose your place in the cycle. Return `next_kind` from `/sessions/{id}/complete` and,
when `auto_start_breaks` is on, let the client immediately start the next session.

**Notifications** — request permission on a real user gesture (a toggle in settings), never on page
load, or Chrome suppresses it and the user never sees the prompt again. Fire on session end via the
Notifications API, plus a short sound gated on `sound_enabled`. Because audio playback needs a prior
user interaction, unlock the audio element on the first Start click and reuse it.

Keep `document.title` as the always-visible fallback, since permission may be denied.

---

## 4.4 Production

**`backend/Dockerfile`** — multi-stage: build wheels, then copy into `python:3.12-slim`. Non-root
user, `uvicorn` with `--workers`, no `--reload`.

**Do not bump the base image past 3.12 without replacing passlib.** `passlib` 1.7.4 imports the
stdlib `crypt` module, which 3.13 removed — the image would fail at import, not at test time.
Replacing it with `argon2-cffi` directly (already an installed dependency) is the smaller change.

**`frontend/Dockerfile`** — `npm ci && npm run build`, then `nginx:alpine` serving `/usr/share/nginx/html`.

**`frontend/nginx.conf`**
- SPA fallback `try_files $uri $uri/ /index.html`
- `/api` → `api:8000`
- `/ws` → `api:8000` with `Upgrade` / `Connection` headers — **without these the WebSocket silently
  fails in production while working perfectly in dev**, which is the single most common way this
  stack breaks on first deploy
- gzip, long cache on hashed assets, `no-cache` on `index.html`

**`compose.prod.yaml`** — no bind mounts, no reload, secrets from the environment, restart policies,
healthchecks on all three services, Postgres on a named volume with no published port.

Generate `JWT_SECRET` properly (`openssl rand -hex 32`) and set `COOKIE_SECURE=true` — `Settings`
now refuses to boot a deployed config without it.

**Rate-limit `/api/auth/login`.** Section 1.3 deliberately runs argon2 even for unknown addresses,
to close a timing oracle that leaked which emails are registered (measured at 50x before the fix).
The cost of that is that every anonymous login attempt buys ~50–100 ms of CPU, so an unauthenticated
flood saturates the API. A per-IP limiter at the proxy, or `slowapi` in front of the route, closes
it; nothing in phases 1–3 does.
Run migrations as an explicit step (`docker compose run --rm api alembic upgrade head`), not on
app startup — startup migrations race when more than one worker boots.

**Keep the `/ws` access token out of the access log.** Uvicorn logs the request line, so every
WebSocket connect writes `"WebSocket /ws?token=eyJ..."` — a live bearer credential, in plaintext,
verbatim (confirmed at phase 1's exit gate). On a loopback dev box the exposure is bounded by the
access token's few minutes; once logs are shipped to an aggregator it is not. Two ways out, and this
is the section that owns the choice:

- **Redact it** in the production logging config — a filter on `uvicorn.access` that strips the query
  string. Cheap, no protocol change, and it also covers anything else that ever lands in a query.
- **Move the token off the URL** — `Sec-WebSocket-Protocol` (a browser *can* set subprotocols) or a
  single-use ticket fetched over HTTP first. Strictly better, but it changes `main.py`'s `/ws` handler
  and `frontend/src/features/realtime/sessionSocket.ts` together, which is why phase 1 did not do it.

Do the redaction at minimum; it is a few lines and it is in the logging config either way.

Document a `pg_dump` backup one-liner in the README. Postgres in a container with no backup is the
same trap as an ungitignored CSV.

---

## 4.5 Tests

| file | covers |
|---|---|
| `test_goals.py` | category-scoped goal counts only its sessions · progress at period edges |
| `test_goal_periods.py` | Jalali vs Gregorian week boundaries · a 31-day Jalali month |
| `test_streaks.py` | consecutive days · a gap resets · **today pending does not break the streak** · no-goal fallback |
| `test_cycle.py` | `next_kind` follows the round count · long break at the right round |

---

## Definition of done

- [ ] Create a daily goal; dashboard shows live progress and the streak tile fills in
- [ ] A category-scoped goal ignores sessions outside it
- [ ] Weekly period starts Saturday in Jalali mode, Monday in Gregorian
- [ ] A streak with today still in progress reads as unbroken
- [ ] Work → break auto-cycles; the long break lands on the configured round
- [ ] Notification and sound fire on completion; denying permission degrades to the title fallback
- [ ] `docker compose -f compose.prod.yaml up --build` from a clean volume, then migrate, register,
      and complete a full work → break cycle
- [ ] **WebSocket works through nginx in prod**, not just in dev
- [ ] `pytest` green

---

## Todo

**Goals**
- [ ] Migration 0010 — `goals`
- [ ] Period-bounds helper `(scope, date, calendar_pref, tz) → UTC range`, using `jdatetime`
- [ ] Goals CRUD
- [ ] `/goals/progress` with category/task scoping
- [ ] Goals UI: create/edit, progress bars, dashboard tiles

**Streaks**
- [ ] `/analytics/streaks` computed from distinct local days
- [ ] Today treated as neutral, not a miss
- [ ] No-daily-goal fallback to "any session"
- [ ] Streak tile + contribution strip

**Cycle & notifications**
- [ ] Round position derived from the day's completed work sessions
- [ ] `next_kind` on complete; honour `auto_start_breaks`
- [ ] Notification permission on a user gesture only
- [ ] Sound with audio unlocked on first Start; `document.title` fallback

**Production**
- [ ] Multi-stage `backend/Dockerfile`, non-root, workers
- [ ] `frontend/Dockerfile` → nginx static
- [ ] `nginx.conf`: SPA fallback, `/api` proxy, **`/ws` upgrade headers**, gzip, cache policy
- [ ] `compose.prod.yaml` with healthchecks, restart policies, no published DB port
- [ ] Real `JWT_SECRET`, `Secure` cookie
- [ ] Migrations as an explicit step, not on startup
- [ ] Redact the query string from the `uvicorn.access` log (the `/ws` token)
- [ ] Rate-limit `/api/auth/login`
- [ ] README: deploy steps + `pg_dump` backup

**Tests**
- [ ] `test_goals.py`, `test_goal_periods.py`, `test_streaks.py`, `test_cycle.py`

---

## Exit gate

Phase 4 is not finished until every item in the gate checklist in
[`docs/WORKFLOW.md`](../WORKFLOW.md#exit-gate) passes:
todos ticked · `pytest` fully green · `/code-review high` clean over the phase diff ·
Definition of done verified by hand in a browser · a clean-volume rebuild
(`docker compose -f compose.prod.yaml down -v && ... up --build`) walked end to end ·
committed and merged.

`/security-review` is **mandatory** for this phase — it covers secrets, cookie flags, and the nginx/production config.

This is the final phase — at its gate, the app is complete and deployable.
