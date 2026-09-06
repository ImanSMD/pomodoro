# pomodoro-cli

A tiny command-line pomodoro / task-timer for people who use the **Persian
(Jalali) calendar**. One script, one CSV, no server, no notebook.

## Why

Most pomodoro trackers assume the Gregorian calendar and want you to open an
app. This is a single Python file that logs `start`/`end` events straight to
a CSV, dates in Jalali, runnable from any terminal.

## Data model

Each pomodoro session is **one row** in `df_pomodoro.csv`:

| uuid | date | weekday | task | start_hour | start_minute | end_hour | end_minute | mins |
|------|------|---------|------|------------|--------------|----------|------------|------|

- `start` appends a new row with `end_hour` / `end_minute` / `mins` left blank.
- `end` fills those three fields in on that same row.
- A session is attributed to the Jalali day it **started** on, even if it
  runs past midnight.
- If you point the script at an older two-row (`start`/`end` + `state`
  column) log, it auto-migrates the first time you run any command, and
  keeps your original file as `df_pomodoro_legacy_backup.csv`.

## Install

```bash
git clone <your-repo-url>
cd pomodoro-cli
pip install -r requirements.txt
```

If you're on a distro that blocks system-wide pip installs
("externally managed environment"), either add `--break-system-packages`
or use a virtual environment:

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

## Set up a shortcut

**Linux / macOS** — add to `~/.bashrc` or `~/.zshrc`:
```bash
alias pomo="python3 /full/path/to/pomodoro.py"
```
then `source ~/.bashrc`.

**Windows** — create `pomo.bat` next to the script:
```bat
@echo off
python "C:\full\path\to\pomodoro.py" %*
```
and add that folder to your `PATH` (System Properties → Environment
Variables → User variables → `Path` → New).

Data files are always created next to `pomodoro.py` itself, regardless of
which directory you run the command from.

## Usage

```
pomo start <task>              Start a new task
pomo end                       End the currently running task
pomo status                    Show what's running and for how long
pomo cancel                    Discard a task you just started by mistake

pomo fix-end --mins 45               Close a forgotten task, given minutes spent
pomo fix-end --hour 21 --minute 30   Close a forgotten task, given the end time

pomo list                      Show the last 10 log rows
pomo list -n 20                Show the last 20 log rows

pomo agg                       Recompute and show daily/task totals
pomo agg -n 30                 Show more rows of the aggregate

pomo remove -n 3               Remove the last 3 rows (asks to confirm first)
```

Typical day:
```
pomo start coding
# ...work...
pomo end

pomo start meetings
# ...work...
pomo end

pomo agg
```

## Files

- `pomodoro.py` — the whole tool
- `df_pomodoro.csv` — raw log, one row per session (created on first use, gitignored)
- `df_pomodoro_backup.csv` — mirror copy, updated on every save (gitignored)
- `df_pomodoro_agg.csv` — daily/task totals, rebuilt on every `end`/`agg` (gitignored)

Your own tracked data is excluded from version control by `.gitignore` —
only the script itself is meant to be shared/versioned. If you want your
data backed up too, either make the repo private or remove the relevant
lines from `.gitignore`.

## Requirements

- Python 3.8+
- `pandas`, `jdatetime`, `shortuuid`


