#!/usr/bin/env python3
"""
Pomodoro CLI tracker — Persian (Jalali) calendar, one row per session.

Install once:
    pip install pandas jdatetime shortuuid

Typical use:
    python pomodoro.py start mlflow
    python pomodoro.py status
    python pomodoro.py end
    python pomodoro.py list
    python pomodoro.py agg

Data model
----------
Each pomodoro session is ONE row:
    uuid, date, weekday, task, start_hour, start_minute, end_hour, end_minute, mins
'start' appends a row with end_hour/end_minute/mins left blank.
'end' fills those three fields in on the same row (the last row still
missing 'mins'). A session is attributed to the Jalali day it *started* on,
even if it happens to run past midnight.

If you point this script at a CSV still in the old two-row
(start/end + 'state' column) format, it is auto-migrated the first time you
run any command: your original file is preserved as
df_pomodoro_legacy_backup.csv and the new-format file replaces df_pomodoro.csv.

Data files (created on first use) live in the same folder as this script
by default, no matter which directory you run it from, or wherever
POMODORO_DIR points to if you set that environment variable:
    df_pomodoro.csv         raw log, one row per session
    df_pomodoro_backup.csv  mirror copy, written on every save
    df_pomodoro_agg.csv     daily/task aggregate, rebuilt on every 'end'/'agg'

Tip: alias this in your shell profile so you don't type the full path:
    alias pomo="python3 /full/path/to/pomodoro.py"
Then: pomo start coding / pomo end / pomo status
"""
import argparse
import os
from datetime import timedelta
from pathlib import Path
from warnings import filterwarnings

import pandas as pd
import shortuuid
from jdatetime import datetime as jdt

filterwarnings('ignore')

DATA_DIR = Path(os.environ.get('POMODORO_DIR', Path(__file__).resolve().parent))
CSV_PATH = DATA_DIR / 'df_pomodoro.csv'
BACKUP_PATH = DATA_DIR / 'df_pomodoro_backup.csv'
AGG_PATH = DATA_DIR / 'df_pomodoro_agg.csv'
LEGACY_BACKUP_PATH = DATA_DIR / 'df_pomodoro_legacy_backup.csv'

COLUMNS = ['uuid', 'date', 'weekday', 'task',
           'start_hour', 'start_minute', 'end_hour', 'end_minute', 'mins']

WEEKDAYS = {0: 'Saturday', 1: 'Sunday', 2: 'Monday', 3: 'Tuesday',
            4: 'Wednesday', 5: 'Thursday', 6: 'Friday'}


# --------------------------------------------------------------------------
# Data access
# --------------------------------------------------------------------------

def migrate_legacy(df_old):
    """Convert the old event-log (start/end rows + 'state' column) into
    one row per session, pairing rows by uuid."""
    df_old = df_old.copy()
    df_old['uuid'] = df_old['uuid'].astype(str)
    rows = []
    for uuid_val, grp in df_old.groupby('uuid', sort=False):
        start_rows = grp[grp.state == 'start']
        end_rows = grp[grp.state == 'end']
        if len(start_rows) == 0:
            continue  # orphan end row with no start — shouldn't happen, skip
        start = start_rows.iloc[0]
        if len(end_rows) > 0:
            end = end_rows.iloc[-1]
            end_hour, end_minute, mins = end.hour, end.minute, end.mins
        else:
            end_hour, end_minute, mins = None, None, None
        rows.append({
            'uuid': uuid_val,
            'date': start.date,
            'weekday': start.weekday,
            'task': start.task,
            'start_hour': start.hour,
            'start_minute': start.minute,
            'end_hour': end_hour,
            'end_minute': end_minute,
            'mins': mins,
        })
    return pd.DataFrame(rows, columns=COLUMNS)


def load_df():
    if not CSV_PATH.exists():
        return pd.DataFrame(columns=COLUMNS)
    df = pd.read_csv(CSV_PATH, dtype={'uuid': str})
    if 'state' in df.columns and 'start_hour' not in df.columns:
        df.to_csv(LEGACY_BACKUP_PATH, index=False)
        df = migrate_legacy(df)
        save_df(df)
        print(f"[one-time migration] Converted old event-log format to "
              f"one-row-per-session. Your original file is safe at "
              f"{LEGACY_BACKUP_PATH.name}\n")
    return df


def save_df(df):
    df.to_csv(CSV_PATH, index=False)
    df.to_csv(BACKUP_PATH, index=False)


def get_open_task(df):
    """Return (index, row) of the last session still missing 'mins', or
    (None, None) if nothing is running."""
    if df.empty:
        return None, None
    idx = df.index[-1]
    last = df.loc[idx]
    if pd.isna(last['mins']):
        return idx, last
    return None, None


def jalali_start_dt(row):
    date_int = int(row.date)
    year, month, day = date_int // 10000, (date_int // 100) % 100, date_int % 100
    return jdt(year, month, day, int(row.start_hour), int(row.start_minute))


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_start(args):
    df = load_df()
    _, open_task = get_open_task(df)
    if open_task is not None:
        print(f"A task is already running: '{open_task.task}' "
              f"(started {int(open_task.start_hour):02d}:{int(open_task.start_minute):02d}). "
              f"Run 'end' to finish it, or 'cancel' to discard it.")
        return

    now = jdt.now()
    row = {
        'uuid': shortuuid.ShortUUID().random(length=10),
        'date': int(now.date().strftime('%Y%m%d')),
        'weekday': WEEKDAYS[now.weekday()],
        'task': args.task,
        'start_hour': int(now.strftime('%H')),
        'start_minute': int(now.strftime('%M')),
        'end_hour': None,
        'end_minute': None,
        'mins': None,
    }
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    save_df(df)
    print(f"Started '{args.task}' at {row['start_hour']:02d}:{row['start_minute']:02d} "
          f"({row['weekday']}, {row['date']})")


def cmd_end(args):
    df = load_df()
    idx, open_task = get_open_task(df)
    if open_task is None:
        print("No running task to end. Start one first with: start <task>")
        return

    start_dt = jalali_start_dt(open_task)
    now = jdt.now()
    elapsed_min = (now - start_dt).total_seconds() / 60

    df.loc[idx, 'end_hour'] = int(now.strftime('%H'))
    df.loc[idx, 'end_minute'] = int(now.strftime('%M'))
    df.loc[idx, 'mins'] = round(elapsed_min)
    save_df(df)
    pomodoro_agg()

    mins = df.loc[idx, 'mins']
    h, m = divmod(int(mins), 60)
    duration = f"{h}h {m}m" if h else f"{m}m"
    print(f"Ended '{open_task.task}' — {duration} "
          f"({int(df.loc[idx, 'end_hour']):02d}:{int(df.loc[idx, 'end_minute']):02d})")


def cmd_status(args):
    df = load_df()
    _, open_task = get_open_task(df)
    if open_task is None:
        print("No task currently running.")
        return
    start_dt = jalali_start_dt(open_task)
    elapsed_min = (jdt.now() - start_dt).total_seconds() / 60
    h, m = divmod(int(elapsed_min), 60)
    duration = f"{h}h {m}m" if h else f"{m}m"
    print(f"'{open_task.task}' running for {duration} "
          f"(started {int(open_task.start_hour):02d}:{int(open_task.start_minute):02d})")


def cmd_cancel(args):
    df = load_df()
    idx, open_task = get_open_task(df)
    if open_task is None:
        print("No running task to cancel.")
        return
    df = df.drop(index=idx)
    save_df(df)
    print(f"Cancelled '{open_task.task}' (session discarded).")


def cmd_fix_end(args):
    """Close a task you forgot to 'end', either by giving the clock time
    it actually ended or the number of minutes spent."""
    df = load_df()
    idx, open_task = get_open_task(df)
    if open_task is None:
        print("No open task to fix.")
        return

    start_dt = jalali_start_dt(open_task)

    if args.hour is not None and args.minute is not None:
        date_int = int(open_task.date)
        year, month, day = date_int // 10000, (date_int // 100) % 100, date_int % 100
        end_dt = jdt(year, month, day, args.hour, args.minute)
        if end_dt < start_dt:  # crossed midnight
            end_dt = end_dt + timedelta(days=1)
        mins = round((end_dt - start_dt).total_seconds() / 60)
        hour, minute = args.hour, args.minute
    elif args.mins is not None:
        mins = args.mins
        end_dt = start_dt + timedelta(minutes=mins)
        hour, minute = end_dt.hour, end_dt.minute
    else:
        print("Provide --mins, or both --hour and --minute.")
        return

    df.loc[idx, 'end_hour'] = hour
    df.loc[idx, 'end_minute'] = minute
    df.loc[idx, 'mins'] = mins
    save_df(df)
    pomodoro_agg()
    print(f"Closed '{open_task.task}' with {mins} min, ending at {hour:02d}:{minute:02d}")


def cmd_list(args):
    df = load_df()
    if df.empty:
        print("No records yet.")
        return
    print(df.tail(args.n).to_string(index=False))


def pomodoro_agg():
    df = load_df()
    if df.empty:
        return
    closed = df.dropna(subset=['mins'])
    if closed.empty:
        return
    df_agg = closed.groupby(['date', 'weekday', 'task'], as_index=False).agg({'mins': 'sum'})
    daily_totals = (df_agg.groupby('date', as_index=False)
                    .agg({'mins': 'sum'})
                    .rename(columns={'mins': 'total_daily_mins'}))
    df_agg_final = daily_totals.merge(df_agg, on='date')
    df_agg_final = df_agg_final.rename(columns={'mins': 'total_task_mins'})
    df_agg_final['total_task_mins'] = df_agg_final['total_task_mins'].astype(int)
    df_agg_final['total_daily_mins'] = df_agg_final['total_daily_mins'].astype(int)
    df_agg_final = df_agg_final.sort_values(by='date', ascending=False)
    df_agg_final.to_csv(AGG_PATH, index=False)


def cmd_agg(args):
    pomodoro_agg()
    if AGG_PATH.exists():
        df = pd.read_csv(AGG_PATH)
        print(df.head(args.n).to_string(index=False))
    else:
        print("No data to aggregate yet.")


def cmd_remove(args):
    df = load_df()
    if df.empty:
        print("Nothing to remove.")
        return
    print(df.tail(args.n).to_string(index=False))
    confirm = input(f"\nRemove these {args.n} row(s)? [y/N] ")
    if confirm.strip().lower() != 'y':
        print("Cancelled, nothing removed.")
        return
    df = df.iloc[:-args.n]
    save_df(df)
    pomodoro_agg()
    print("Done. New tail:")
    print(df.tail().to_string(index=False))


# --------------------------------------------------------------------------
# CLI wiring
# --------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog='pomodoro',
        description="Pomodoro CLI tracker (Jalali/Persian calendar dates).")
    sub = p.add_subparsers(dest='command', required=True)

    sp = sub.add_parser('start', help='Start a new task')
    sp.add_argument('task', help='Task name')
    sp.set_defaults(func=cmd_start)

    sp = sub.add_parser('end', help='End the currently running task')
    sp.set_defaults(func=cmd_end)

    sp = sub.add_parser('status', help='Show the currently running task, if any')
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser('cancel', help='Discard the currently running (unended) task')
    sp.set_defaults(func=cmd_cancel)

    sp = sub.add_parser('fix-end', help='Manually close a forgotten task')
    sp.add_argument('--mins', type=float, default=None, help='Minutes spent on the task')
    sp.add_argument('--hour', type=int, default=None, help='End hour, 0-23')
    sp.add_argument('--minute', type=int, default=None, help='End minute, 0-59')
    sp.set_defaults(func=cmd_fix_end)

    sp = sub.add_parser('list', help='Show the last N raw log rows')
    sp.add_argument('-n', type=int, default=10, help='Number of rows (default 10)')
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser('agg', help='Recompute and show daily/task aggregates')
    sp.add_argument('-n', type=int, default=15, help='Number of rows to show (default 15)')
    sp.set_defaults(func=cmd_agg)

    sp = sub.add_parser('remove', help='Remove the last N raw rows (asks for confirmation)')
    sp.add_argument('-n', type=int, required=True, help='Number of rows to remove')
    sp.set_defaults(func=cmd_remove)

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
