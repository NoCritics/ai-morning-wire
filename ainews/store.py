"""Day files, bookmarks, the seen-story list and the run log."""
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

ITEM_TYPES = (
    "model-release",
    "quantization-local",
    "tooling",
    "research",
    "product-api",
    "benchmark-eval",
    "industry",
)
PR_NOTE_KINDS = ("context", "background", "careful")
LEGACY_STORY_URLS_FIELD = "alphasignal_urls"
FEED_DAYS = 7
ARCHIVE_DAYS = 30
DAY_FILE_NAME = re.compile(r"(\d{4}-\d{2}-\d{2})\.json")
DAY_FOLDER_NAME = re.compile(r"\d{4}-\d{2}-\d{2}")
ITEM_ID = re.compile(r"[a-z0-9][a-z0-9-]*")


class DataError(Exception):
    """A data file is malformed. The message names the file and the problem."""


def require_text(record, field_name, where):
    value = record.get(field_name)
    if not isinstance(value, str) or value.strip() == "":
        raise DataError(f"{where}: '{field_name}' must be a non-empty string")


def validate_item(item, where):
    if not isinstance(item, dict):
        raise DataError(f"{where}: an item must be a JSON object")
    for field_name in ("id", "title", "type", "published"):
        require_text(item, field_name, where)
    if not ITEM_ID.fullmatch(item["id"]):
        raise DataError(f"{where}: id '{item['id']}' may only use a-z, 0-9 and '-'")
    if item["type"] not in ITEM_TYPES:
        raise DataError(f"{where}: type '{item['type']}' is not one of: {', '.join(ITEM_TYPES)}")
    try:
        date.fromisoformat(item["published"])
    except ValueError:
        raise DataError(f"{where}: 'published' must be a YYYY-MM-DD date") from None
    bullets = item.get("bullets")
    if not isinstance(bullets, list) or len(bullets) < 3 or len(bullets) > 5:
        raise DataError(f"{where}: 'bullets' must hold 3 to 5 entries")
    for bullet in bullets:
        if not isinstance(bullet, str) or bullet.strip() == "":
            raise DataError(f"{where}: every bullet must be a non-empty string")
    sources = item.get("sources")
    if not isinstance(sources, list) or len(sources) == 0:
        raise DataError(f"{where}: 'sources' must list at least one source")
    for source in sources:
        if not isinstance(source, dict):
            raise DataError(f"{where}: every source must be a JSON object")
        require_text(source, "label", where)
        if not str(source.get("url", "")).startswith(("http://", "https://")):
            raise DataError(f"{where}: every source needs an http(s) 'url'")
    story_urls = item.get("story_urls")
    if not isinstance(story_urls, list) or len(story_urls) == 0:
        raise DataError(f"{where}: 'story_urls' must list at least one story URL")
    if "pr_check" in item:
        validate_pr_check(item["pr_check"], where)


def validate_pr_check(pr_check, where):
    """The optional 'Without the PR' block: why it was checked, what holds up, notes and the quoted pitch."""
    if not isinstance(pr_check, dict):
        raise DataError(f"{where}: 'pr_check' must be a JSON object")
    require_text(pr_check, "why", f"{where} pr_check")
    require_text(pr_check, "holds_up", f"{where} pr_check")
    notes = pr_check.get("notes")
    if not isinstance(notes, list) or len(notes) < 1 or len(notes) > 4:
        raise DataError(f"{where} pr_check: 'notes' must hold 1 to 4 notes")
    for note in notes:
        if not isinstance(note, dict) or note.get("kind") not in PR_NOTE_KINDS:
            raise DataError(f"{where} pr_check: every note's 'kind' must be context, background or careful")
        require_text(note, "text", f"{where} pr_check")
    if "pitch" in pr_check:
        require_text(pr_check, "pitch", f"{where} pr_check")


def upgrade_legacy_item(item):
    """Items saved before Hacker News and paper discovery named their story URLs 'alphasignal_urls'."""
    if isinstance(item, dict) and "story_urls" not in item and LEGACY_STORY_URLS_FIELD in item:
        item["story_urls"] = item.pop(LEGACY_STORY_URLS_FIELD)
    return item


def read_json(path):
    path = Path(path)
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise DataError(f"{path.name}: not valid JSON ({error})") from None
    except OSError as error:
        raise DataError(f"{path.name}: could not be read ({error})") from None


def load_day(path):
    path = Path(path)
    name_match = DAY_FILE_NAME.fullmatch(path.name)
    if name_match is None:
        raise DataError(f"{path.name}: day files must be named YYYY-MM-DD.json")
    day = read_json(path)
    if not isinstance(day, dict) or day.get("date") != name_match.group(1):
        raise DataError(f"{path.name}: 'date' must equal {name_match.group(1)}")
    if not isinstance(day.get("items"), list):
        raise DataError(f"{path.name}: 'items' must be a list")
    for index, item in enumerate(day["items"]):
        upgrade_legacy_item(item)
        validate_item(item, f"{path.name} item {index + 1}")
    return day


def load_days(days_dir):
    """Every day file, validated, newest first. Item ids must be unique across all days."""
    days = []
    for path in sorted(Path(days_dir).glob("*.json"), reverse=True):
        days.append(load_day(path))
    used_ids = set()
    for day in days:
        for item in day["items"]:
            if item["id"] in used_ids:
                raise DataError(f"{day['date']}.json: item id '{item['id']}' is used more than once")
            used_ids.add(item["id"])
    return days


def published_story_urls(days):
    """Every story URL that an item in these days already covers."""
    story_urls = set()
    for day in days:
        for item in day["items"]:
            for story_url in item["story_urls"]:
                story_urls.add(story_url)
    return story_urls


def write_text_atomic(path, text):
    """Write through a temporary file so a crash never leaves a half-written file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # A unique name: two processes writing the same file must not share one temporary file.
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(temporary_name, path)
    except BaseException:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
        raise


def write_json_atomic(path, data):
    write_text_atomic(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def day_age(day_date_text, today):
    return (today - date.fromisoformat(day_date_text)).days


def apply_retention(days_dir, today):
    """Delete day files older than ARCHIVE_DAYS. Returns the deleted dates, oldest first."""
    deleted_dates = []
    for path in sorted(Path(days_dir).glob("*.json")):
        name_match = DAY_FILE_NAME.fullmatch(path.name)
        if name_match is None:
            continue
        if day_age(name_match.group(1), today) > ARCHIVE_DAYS:
            path.unlink()
            deleted_dates.append(name_match.group(1))
    return deleted_dates


def prune_work_dirs(work_dir, today):
    """Delete work/<date> run folders (scraped sources, replies, drafts) older than ARCHIVE_DAYS.

    Other folders, such as work/publish, are left alone. Returns the deleted dates, oldest first.
    """
    deleted_dates = []
    if not Path(work_dir).is_dir():
        return deleted_dates
    for folder in sorted(Path(work_dir).iterdir()):
        if not folder.is_dir() or DAY_FOLDER_NAME.fullmatch(folder.name) is None:
            continue
        if day_age(folder.name, today) > ARCHIVE_DAYS:
            shutil.rmtree(folder)
            deleted_dates.append(folder.name)
    return deleted_dates


def read_run_log(log_path):
    """The run log's text, or "" when there is no log yet."""
    log_path = Path(log_path)
    if not log_path.exists():
        return ""
    return log_path.read_text(encoding="utf-8", errors="replace")


def compact_json(data):
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def prune_run_log(log_path, today):
    """Drop run-log lines older than ARCHIVE_DAYS. Returns how many lines were dropped."""
    log_path = Path(log_path)
    kept_lines = []
    dropped_count = 0
    for line in read_run_log(log_path).splitlines():
        line_date = line[:10]
        if DAY_FOLDER_NAME.fullmatch(line_date) and day_age(line_date, today) > ARCHIVE_DAYS:
            dropped_count += 1
        else:
            kept_lines.append(line)
    if dropped_count > 0:
        kept_text = ""
        for line in kept_lines:
            kept_text += line + "\n"
        write_text_atomic(log_path, kept_text)
    return dropped_count


def load_seen(seen_path):
    if not Path(seen_path).exists():
        return {}
    seen = read_json(seen_path)
    if not isinstance(seen, dict):
        raise DataError(f"{Path(seen_path).name}: must be a JSON object of story URL to date")
    return seen


def update_seen(seen_path, seen, days, today):
    """Record every story URL found in the day files in the seen entries already loaded; forget entries older than ARCHIVE_DAYS."""
    for day in days:
        for item in day["items"]:
            for story_url in item["story_urls"]:
                if story_url not in seen:
                    seen[story_url] = day["date"]
    kept = {}
    for story_url, collected_date in seen.items():
        if day_age(collected_date, today) <= ARCHIVE_DAYS:
            kept[story_url] = collected_date
    write_json_atomic(seen_path, kept)
    return kept


def validate_bookmark(bookmark, where):
    validate_item(bookmark, where)
    require_text(bookmark, "bookmarked_at", where)


def upgrade_and_validate_bookmarks(bookmarks, file_name):
    for index, bookmark in enumerate(bookmarks):
        upgrade_legacy_item(bookmark)
        validate_bookmark(bookmark, f"{file_name} entry {index + 1}")


def load_bookmarks(bookmarks_path):
    if not Path(bookmarks_path).exists():
        return []
    bookmarks = read_json(bookmarks_path)
    upgrade_and_validate_bookmarks(bookmarks, "bookmarks.json")
    return bookmarks


def read_bookmark_export(export_path):
    """The claude.ai page's bookmarks, validated and newest first: a JSON array of item objects."""
    export_name = Path(export_path).name
    exported = read_json(export_path)
    if not isinstance(exported, list):
        raise DataError(f"{export_name}: expected a JSON array of bookmarked items")
    upgrade_and_validate_bookmarks(exported, export_name)
    exported.sort(key=lambda bookmark: bookmark["bookmarked_at"], reverse=True)
    return exported


def use_utf8_console():
    """Headlines carry characters the Windows console code page cannot print; never crash on them."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def append_run_log(log_path, message, now=None):
    if now is None:
        now = datetime.now()
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # One line per call: a title or error text with a line break must not be able to forge a second log line.
    message = " ".join(message.split())
    with open(log_path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"{now:%Y-%m-%d %H:%M} {message}\n")
