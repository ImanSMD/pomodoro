# Phase 3 — Analytics, Calendar, Jalali/Gregorian Toggle

**Outcome:** the payoff phase. A dashboard showing where your time actually goes, a month calendar
of sessions, and a switch that flips every date in the app between Persian and Gregorian.

This replaces `df_pomodoro_agg.csv` — which the old CLI rebuilt from scratch on every `end` — with
real queries over `sessions`.

> **Workflow:** **Do not start this phase until Phase 2 has passed its exit gate.** Work through the sections in order, running
> **code → tests → code review → fix** on each one until a review pass comes back clean.
> See [`docs/WORKFLOW.md`](../WORKFLOW.md) for the full rules.

---

## 3.1 The timezone rule (read before writing any query)

Every aggregate in this phase buckets by the user's **local** day, and a session belongs to the day
it **started** on — the rule the original CLI followed by attributing a session to its start date.

```sql
date_trunc('day', s.started_at AT TIME ZONE :user_tz) AS local_day
```

Bucketing in UTC instead is the defining bug of this phase, and the error window is narrower and
less intuitive than it first looks. For `Asia/Tehran` (UTC+03:30, no DST), local day D spans UTC
`[D-1 20:30, D 20:30)`. So UTC bucketing mislabels exactly the local times **00:00–03:29**, pushing
them onto the **previous** day:

| local (Tehran) | UTC instant | UTC bucket | correct bucket | |
|---|---|---|---|---|
| 2026-03-10 01:00 | 2026-03-09 21:30 | 2026-03-09 | 2026-03-10 | **wrong** |
| 2026-03-10 03:29 | 2026-03-09 23:59 | 2026-03-09 | 2026-03-10 | **wrong** |
| 2026-03-10 20:30 | 2026-03-10 17:00 | 2026-03-10 | 2026-03-10 | ok |
| 2026-03-10 23:30 | 2026-03-10 20:00 | 2026-03-10 | 2026-03-10 | ok |

Note the last row: a late-night session is bucketed **correctly** by naive UTC. Any test written
around 23:30 local passes whether or not the bug is present, which makes it worse than no test.
Every fixture in this section must land in the 00:00–03:29 local window. Put the bucketing expression in **one** helper in
`app/api/routes/analytics.py` and build every endpoint from it.

Ranges from the client arrive as ISO instants, already converted from whatever calendar was on
screen. The API speaks UTC and only UTC.

---

## 3.2 Endpoints

```
GET /api/analytics/summary?from=&to=
    → {total_minutes, session_count, avg_session_minutes,
       completed_sessions, cancelled_sessions,
       by_day: [{day, minutes, count}],
       by_category: [{category_id, name, color, minutes}],
       by_task: [{task_id, title, minutes}]}

GET /api/analytics/heatmap?year=1404      → [{day, minutes}] for a full year
GET /api/analytics/by-hour?from=&to=      → [{hour, minutes}] — when you actually focus
GET /api/calendar?from=&to=               → sessions grouped by local day, with task + category
```

Only `status = 'completed'` counts toward totals — cancelled sessions are surfaced as a count, never
folded into minutes. This mirrors the old `dropna(subset=['mins'])` in `pomodoro_agg()`.

`year` on the heatmap is a **Jalali** year when `calendar_pref = 'jalali'`. Resolve its UTC bounds
server-side rather than having the client guess at the range, since Jalali year boundaries don't
align with Gregorian ones.

**Indexes** (migration 0008): `sessions (user_id, started_at DESC)` and a partial
`sessions (user_id, started_at) WHERE status = 'completed'`. Without them the heatmap scans the
whole table once the log grows past a few thousand rows.

---

## 3.3 `lib/date.ts` — the single formatting chokepoint

```ts
formatDate(iso, fmt?)      formatTime(iso)      formatDateTime(iso)
formatDuration(minutes)    // "1h 25m" — matches the CLI's old output
monthGrid(year, month)     // calendar cells in the active calendar
startOfLocalDay(iso)       weekdayNames()       // Saturday-first for Jalali
```

Each reads `user.calendar_pref` and delegates to `date-fns-jalali` or `date-fns`. **Every** rendered
date in the app goes through here — one component calling `toLocaleDateString()` directly is how the
toggle ends up 90% working, which is worse than not having it.

Two details worth getting right: the Jalali week starts **Saturday** (matching the old `WEEKDAYS`
map where index 0 is Saturday), and Persian digits are a separate concern from the calendar — decide
once whether numerals are localised and apply it consistently.

---

## 3.4 Frontend

**Dashboard** (`features/analytics/`)
- Range picker: today / this week / this month / custom, rendered in the active calendar.
- Stat tiles: total focus time, sessions, average length, current streak (streak lands in Phase 4 —
  leave the tile wired to a placeholder endpoint).
- Daily bar chart, category donut with each category's own colour, top-tasks bar, hour-of-day chart.
- Year heatmap, GitHub-style, Saturday-first rows in Jalali mode.

**Calendar** (`features/calendar/`)
- Month grid from `monthGrid()`, each day showing total minutes and session blocks tinted by
  category; click a day for its session list; click a session to open its task.
- Month/week toggle.

**Charts** — one shared theme module (colours, axis styling, tooltip, empty state) so every chart
reads as one system. Category colours come from the data, everything else from the theme. Give each
chart a real empty state; a new account hits every one of them with zero rows.

**Settings** — the calendar toggle plus timezone, both `PATCH /api/settings`, invalidating all
analytics queries on change since the buckets shift.

---

## 3.5 Tests

| file | covers |
|---|---|
| `test_analytics_tz.py` | **the critical one** — a session at **01:00** Tehran local (21:30 UTC the previous day) buckets on its local day, not the UTC one; the same instant buckets differently for users in different timezones |
| `test_analytics.py` | totals exclude cancelled · by_category sums to total · empty range returns zeros, not an error |
| `test_calendar.py` | a session crossing local midnight appears once, on its start day |
| `test_heatmap.py` | a Jalali year resolves to the right UTC bounds |

Seed fixtures inside the 00:00–03:29 local window, where UTC and local days actually disagree.
Fixtures outside it pass with or without the bug.

---

## Definition of done

- [ ] Dashboard renders totals, daily bars, category split, top tasks, hour-of-day, heatmap
- [ ] **Log a session at 01:00 local** — it appears on *that* day in the calendar and the chart, not the day before
- [ ] Flip the calendar toggle: every date in the app changes, including chart axes and the heatmap
- [ ] Week starts Saturday in Jalali mode
- [ ] Category colours match between the sidebar, calendar blocks, and the donut
- [ ] A brand-new account shows empty states everywhere, no crashes, no `NaN`
- [ ] `pytest` green, timezone tests included

---

## Todo

**Backend**
- [ ] Migration 0008 — analytics indexes on `sessions`
- [ ] `local_day` bucketing helper — written once, used by all endpoints
- [ ] `/analytics/summary` (by_day, by_category, by_task)
- [ ] `/analytics/heatmap` with Jalali-year bound resolution
- [ ] `/analytics/by-hour`
- [ ] `/calendar` grouped by local day
- [ ] Exclude cancelled from minutes, report as a count

**Date layer**
- [ ] `lib/date.ts` with the full helper set
- [ ] Saturday-first weekdays for Jalali
- [ ] Decide and apply the Persian-numerals rule
- [ ] Audit every existing component onto the helpers

**Frontend**
- [ ] Shared chart theme module + empty states
- [ ] Range picker in the active calendar
- [ ] Stat tiles (streak tile stubbed for Phase 4)
- [ ] Daily bars · category donut · top tasks · hour-of-day · year heatmap
- [ ] Month/week calendar with day drill-down
- [ ] Settings: calendar toggle + timezone, invalidating analytics caches

**Tests**
- [ ] `test_analytics_tz.py` with midnight-boundary fixtures
- [ ] `test_analytics.py`, `test_calendar.py`, `test_heatmap.py`

---

## Exit gate

Phase 3 is not finished until every item in the gate checklist in
[`docs/WORKFLOW.md`](../WORKFLOW.md#exit-gate) passes:
todos ticked · `pytest` fully green · `/code-review high` clean over the phase diff ·
Definition of done verified by hand in a browser · a clean-volume rebuild
(`docker compose down -v && docker compose up --build`) walked end to end · committed and merged.

No new auth surface, but every analytics endpoint must still be covered by the cross-user scoping test.

**Phase 4 does not begin until this gate passes.**
