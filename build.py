"""Validate the data, apply retention and write the page.

Usage: python build.py [--root DIR] [--today YYYY-MM-DD] [--import-bookmarks FILE]
Writes ai-news.html (open locally) and work/publish/ (publish to claude.ai: the page and its side files).
"""
import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

from ainews import config, render, runstate, store


def write_publish_folder(publish_dir, template_dir, feed_days, archive_days, bookmarks, page):
    """The small page plus its side files: the template's css and js, the bookmarks and one file per day.

    files.json lists the side files (not itself, not the page) relative to publish_dir. Day files of days
    that are no longer shown are deleted.
    """
    side_file_texts = {
        "page.css": (template_dir / "page.css").read_text(encoding="utf-8"),
        "page.js": (template_dir / "page.js").read_text(encoding="utf-8"),
        "bookmarks.json": store.compact_json(bookmarks),
    }
    for day in feed_days + archive_days:
        side_file_texts[f"days/{day['date']}.json"] = store.compact_json(day)
    for relative_path, text in side_file_texts.items():
        store.write_text_atomic(publish_dir / relative_path, text)
    store.write_text_atomic(publish_dir / "files.json", store.compact_json(sorted(side_file_texts)))
    store.write_text_atomic(publish_dir / "ai-news.html", page)
    for day_file in (publish_dir / "days").glob("*.json"):
        if f"days/{day_file.name}" not in side_file_texts:
            day_file.unlink()


def build(root, today, import_bookmarks_path=None, settings=None):
    """Returns a one-line summary. Raises store.DataError and leaves everything untouched.

    All data, seen.json included, is validated before the bookmark import, retention or pruning change anything. A bookmarks file
    that the import is about to replace is not read. A broken config.toml does not stop the build: the page
    is written without a digest time, so it can still show the failure.
    """
    data_dir = root / "data"
    loaded_days = store.load_days(data_dir / "days")
    seen = store.load_seen(data_dir / "seen.json")
    if import_bookmarks_path is not None:
        bookmarks = store.read_bookmark_export(import_bookmarks_path)
        store.write_json_atomic(data_dir / "bookmarks.json", bookmarks)
    else:
        bookmarks = store.load_bookmarks(data_dir / "bookmarks.json")
    deleted_dates = store.apply_retention(data_dir / "days", today)
    pruned_work_dates = store.prune_work_dirs(root / "work", today)
    store.prune_run_log(data_dir / "runs.log", today)
    days = []
    for day in loaded_days:
        if day["date"] not in deleted_dates:
            days.append(day)
    if settings is None:
        try:
            settings = config.load_config(root)
        except config.ConfigError:
            settings = None
    digest_time = None
    if settings is not None:
        digest_time = settings["time"]
    day_status = runstate.day_status(store.read_run_log(data_dir / "runs.log"), today)
    status = {
        "result": day_status["result"],
        "items": day_status["items"],
        "failedReason": day_status["failed_reason"],
    }
    feed_days, archive_days = render.split_days(days, today)
    store.write_text_atomic(
        root / "ai-news.html",
        render.standalone_page(render.render_page(root / "template", feed_days, archive_days, bookmarks, today, status, digest_time)),
    )
    feed_dates = []
    for day in feed_days:
        feed_dates.append(day["date"])
    archive_dates = []
    for day in archive_days:
        archive_dates.append(day["date"])
    write_publish_folder(
        root / "work" / "publish",
        root / "template",
        feed_days,
        archive_days,
        bookmarks,
        render.render_published_page(root / "template", feed_dates, archive_dates, today, status, digest_time),
    )
    store.update_seen(data_dir / "seen.json", seen, days, today)
    feed_item_count = 0
    for day in feed_days:
        feed_item_count += len(day["items"])
    summary = (
        f"built: {len(feed_days)} feed days ({feed_item_count} items), "
        f"{len(archive_days)} archive days, {len(bookmarks)} bookmarks"
    )
    if deleted_dates:
        summary += f"; deleted days: {', '.join(deleted_dates)}"
    if pruned_work_dates:
        summary += f"; deleted work folders: {', '.join(pruned_work_dates)}"
    return summary


def report_stopped_build(log_path, error):
    store.append_run_log(log_path, f"build stopped: {error}")
    print(f"Build stopped, page left unchanged: {error}", file=sys.stderr)


def main():
    store.use_utf8_console()
    parser = argparse.ArgumentParser(description="Build the AI news page from data/.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--today", type=date.fromisoformat, default=date.today())
    parser.add_argument("--import-bookmarks", type=Path, default=None)
    arguments = parser.parse_args()
    log_path = arguments.root / "data" / "runs.log"
    if arguments.today > date.today() + timedelta(days=1):
        print(f"Build stopped, nothing changed: --today {arguments.today} is in the future, which would delete every day file", file=sys.stderr)
        return 1
    try:
        summary = build(arguments.root, arguments.today, arguments.import_bookmarks)
    except store.DataError as error:
        report_stopped_build(log_path, error)
        return 1
    store.append_run_log(log_path, summary)
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
