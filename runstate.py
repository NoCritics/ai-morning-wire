"""Run-log decisions and lines for the scheduled morning run.

Usage: python runstate.py [--root DIR] next-action
       python runstate.py [--root DIR] started | retry | mark-failed
       python runstate.py [--root DIR] published DATE ITEMS BATCHES
       python runstate.py [--root DIR] failed DATE REASON
next-action prints "<action>: <reason>" (launch, retry, mark-failed or nothing); exit 3 means config.toml is broken.
--ignore-time treats the scheduled time as 00:00 (setup's first run); every other rule stays.
"""
import argparse
import sys
from datetime import date, datetime, time
from pathlib import Path

import build
from ainews import config, runstate, store

WATCHDOG_REASON = "the run did not finish (Claude Code may be logged out, out of usage, closed or waiting for input)"


def rebuild_page_after_failure(root, log_path, today):
    """Rebuilds the page so it shows today's failure. Returns False, after logging and printing why, when the build stops."""
    try:
        build.build(root, today)
    except (store.DataError, OSError) as error:
        store.append_run_log(log_path, f"build stopped: {error}")
        print(f"Build stopped, page left unchanged: {error}", file=sys.stderr)
        return False
    return True


def run_next_action(root, log_path, ignore_time):
    now = datetime.now()
    today = now.date()
    log_text = store.read_run_log(log_path)
    open_day = runstate.find_open_earlier_day(log_text, now)
    if open_day is not None:
        store.append_run_log(log_path, f"failed: {open_day}, {WATCHDOG_REASON}", now)
        log_text = store.read_run_log(log_path)
    status = runstate.day_status(log_text, today)
    # A published day or a run still within its time limit needs no decision, so a broken config.toml is not looked at.
    settled = runstate.settled_without_schedule(status, now)
    if settled is not None:
        print(f"{settled[0]}: {settled[1]}")
        return 0
    try:
        settings = config.load_config(root)
    except config.ConfigError as error:
        if not status["config_failed"]:
            store.append_run_log(log_path, f"failed: {today}, config: {error}")
            rebuild_page_after_failure(root, log_path, today)
        print("nothing: config error")
        return 3
    scheduled = datetime.strptime(settings["time"], "%H:%M").time()
    if ignore_time:
        scheduled = time(0, 0)
    action, reason = runstate.next_action(status, now, scheduled)
    print(f"{action}: {reason}")
    return 0


def main():
    store.use_utf8_console()
    parser = argparse.ArgumentParser(description="Decide and log the scheduled morning run.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    commands = parser.add_subparsers(dest="command", required=True)
    next_action_parser = commands.add_parser("next-action")
    next_action_parser.add_argument("--ignore-time", action="store_true")
    commands.add_parser("started")
    commands.add_parser("retry")
    commands.add_parser("mark-failed")
    published_parser = commands.add_parser("published")
    published_parser.add_argument("date", type=date.fromisoformat)
    published_parser.add_argument("items", type=int)
    published_parser.add_argument("batches", type=int)
    failed_parser = commands.add_parser("failed")
    failed_parser.add_argument("date", type=date.fromisoformat)
    failed_parser.add_argument("reason")
    arguments = parser.parse_args()
    root = arguments.root
    log_path = root / "data" / "runs.log"
    today = date.today()
    if arguments.command == "next-action":
        return run_next_action(root, log_path, arguments.ignore_time)
    if arguments.command == "started":
        store.append_run_log(log_path, f"started: {today}")
    elif arguments.command == "retry":
        store.append_run_log(log_path, f"retry: {today}")
    elif arguments.command == "published":
        store.append_run_log(log_path, f"published: {arguments.date}, {arguments.items} items, {arguments.batches} batches")
    elif arguments.command == "failed":
        store.append_run_log(log_path, f"failed: {arguments.date}, {arguments.reason}")
    elif arguments.command == "mark-failed":
        store.append_run_log(log_path, f"failed: {today}, {WATCHDOG_REASON}")
        if not rebuild_page_after_failure(root, log_path, today):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
