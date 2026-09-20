# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current work: CLI → full-stack app

This repo is being rebuilt as a web app — FastAPI + Postgres backend, React frontend, Docker
Compose. The design lives in [`PLAN.md`](PLAN.md), broken into four phase documents under
`docs/phases/`. Read the relevant phase document before writing any code for it.

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

## Legacy CLI

Everything below describes `pomodoro.py`, the original single-file tool. It still runs against its
own CSV and is kept as reference; it is not being migrated (the new app starts with an empty
database). Phase 1 rewrites the rest of this file for the new architecture.

## Project

`pomodoro.py` is the entire tool: a single-file argparse CLI that logs task sessions to CSV using
Persian (Jalali) calendar dates. No build step, no test suite, no linter config, no packaging.

## Commands

```bash
pip install -r requirements.txt          # pandas, jdatetime, shortuuid (Python 3.8+)
python3 pomodoro.py <subcommand> ...     # start | end | status | cancel | fix-end | list | agg | remove
```

Manual testing — **always** set `POMODORO_DIR` to a scratch directory, otherwise the script reads and
writes the user's real log next to `pomodoro.py`:

```bash
POMODORO_DIR=/tmp/pomo-test python3 pomodoro.py start test
POMODORO_DIR=/tmp/pomo-test python3 pomodoro.py end
```

`remove` calls `input()` for confirmation, so it needs a piped `y` or a TTY.

## Architecture

**One row per session.** Columns are fixed in `COLUMNS`: `uuid, date, weekday, task, start_hour,
start_minute, end_hour, end_minute, mins`. `start` appends a row with the last three fields blank;
`end` / `fix-end` fill them in on that same row.

**`mins` is the open/closed sentinel.** `get_open_task()` looks *only at the final row* and treats it
as running iff `mins` is NaN. Everything depends on this: `start` refuses to append when one is open,
`end`/`fix-end`/`cancel` target that index, and `pomodoro_agg()` drops NaN-`mins` rows. Any new
command that appends or reorders rows must keep the open session last.

**Dates are Jalali integers**, `YYYYMMDD` in one `date` column, decoded by integer division in
`jalali_start_dt()`. `weekday` uses the `WEEKDAYS` map where index 0 is Saturday (jdatetime's week
start), not Monday. A session belongs to the Jalali day it *started* on; `fix-end` detects a
past-midnight end time by comparing against the start and adding a day.

**Three derived files, all in `DATA_DIR`** (`POMODORO_DIR` env var, else the script's own directory,
so the alias works from anywhere): `df_pomodoro.csv` is the source of truth, `df_pomodoro_backup.csv`
is a mirror written by every `save_df()`, `df_pomodoro_agg.csv` is rebuilt from scratch by
`pomodoro_agg()`. Mutating commands call `save_df()`, then `pomodoro_agg()` if a closed row changed.
Never write `CSV_PATH` directly — `save_df()` keeps the mirror in sync.

**Legacy migration** runs inside `load_df()`: a CSV with a `state` column and no `start_hour` is the
old two-rows-per-session event log. It is copied to `df_pomodoro_legacy_backup.csv`, folded into the
current shape by `migrate_legacy()` (pairing start/end rows by uuid), and saved back. This is
load-bearing for existing users — keep it working if the schema changes again.

`filterwarnings('ignore')` is set globally at import, so pandas warnings will not surface during
debugging.
