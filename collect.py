"""Find new stories and save their primary sources as raw text.

Usage: python collect.py [--root DIR] [--date YYYY-MM-DD] [--since-hours 48]
Stories come from the sources config.toml turns on: AlphaSignal (its articles' citations), AI stories on
Hacker News, the most upvoted Hugging Face Daily Papers and AI posts of the configured Telegram channels
that link outside Telegram. A story whose source page is already cited, today or in an earlier digest, is
dropped as a duplicate; site front pages never count. max_stories_per_day caps the rest, taking one story
from each source in turn.
Writes work/<date>/stories.json and work/<date>/sources/<story>/<n>-<kind>.txt.
Exit code 2 means every enabled source was unreadable, 3 a bad config.toml; one source failing is only noted.
"""
import argparse
import json
import shutil
import sys
import xml.etree.ElementTree as ElementTree
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ainews import config, discovery, feed, sources, store, urls

MAX_SOURCES_PER_STORY = 5
ALPHASIGNAL = "AlphaSignal"
MAX_SLUG_LENGTH = 80
UNREADABLE_PAGE_ERRORS = (sources.SourceError, json.JSONDecodeError, KeyError, TypeError, ValueError)
SOURCE_SETTING_KEYS = {  # in the order the daily cap takes stories
    ALPHASIGNAL: "alphasignal",
    discovery.HACKER_NEWS: "hacker_news",
    discovery.DAILY_PAPERS: "daily_papers",
    discovery.TELEGRAM: "telegram",
}


class EverySourceUnreadable(Exception):
    """Every source config.toml turns on failed outright. The message lists each source's error."""


def new_record(story, slug, via):
    record = {
        "slug": slug,
        "story_url": story["url"],
        "via": via,
        "headline": story["title"],
        "published": story["published"].date().isoformat(),
        "sources": [],
    }
    if story.get("note"):
        record["note"] = story["note"]
    return record


def unique_slug(slug, used_slugs):
    """The slug as lower-case letters, digits and hyphens cut to MAX_SLUG_LENGTH, with -2, -3, ... added (still within
    the limit) when another story already has it. Slugs name folders, and an AlphaSignal slug comes from a feed URL."""
    slug = urls.slugify(slug, MAX_SLUG_LENGTH)
    if slug == "":
        slug = "story"
    candidate = slug
    number = 2
    while candidate in used_slugs:
        suffix = f"-{number}"
        candidate = slug[:MAX_SLUG_LENGTH - len(suffix)] + suffix
        number += 1
    used_slugs.add(candidate)
    return candidate


def unexpected_error_text(error):
    return f"unexpected error: {type(error).__name__}: {error}"


def save_sources(root, work_dir, record, source_urls, read_source):
    story_dir = work_dir / "sources" / record["slug"]
    if story_dir.resolve().parent != (work_dir / "sources").resolve():
        raise ValueError(f"slug {record['slug']!r} does not name a folder inside {work_dir / 'sources'}")
    if story_dir.exists():
        shutil.rmtree(story_dir)  # files of an earlier run must not reach the summarizer
    for number, source_url in enumerate(source_urls[:MAX_SOURCES_PER_STORY], start=1):
        source_record = {"url": source_url, "kind": sources.classify(source_url), "file": None, "error": None}
        try:
            kind, text = read_source(source_url)
            file_path = story_dir / f"{number}-{kind}.txt"
            store.write_text_atomic(file_path, sources.format_source_file(source_url, text))
            source_record["file"] = file_path.relative_to(root).as_posix()
        except sources.SourceError as error:
            source_record["error"] = str(error)
        except Exception as error:
            source_record["error"] = unexpected_error_text(error)
        record["sources"].append(source_record)


def published_source_urls(root):
    """Normalized URLs of the specific pages (not site front pages) that items already in the digest cite."""
    published_keys = set()
    for day in store.load_days(root / "data" / "days"):
        for item in day["items"]:
            source_urls = []
            for source in item["sources"]:
                source_urls.append(source["url"])
            published_keys.update(urls.specific_page_keys(source_urls))
    return published_keys


def json_source_stories(fetch_page, source_name, url, parse_stories, max_stories, notes, failures):
    """The stories of a JSON source, or [] after noting it and leaving it in failures when it cannot be read."""
    try:
        return parse_stories(json.loads(fetch_page(url)), max_stories)
    except UNREADABLE_PAGE_ERRORS as error:
        notes.append(f"{source_name} unreadable: {error}")
        failures[source_name] = str(error)
        return []


def extra_stories(fetch_page, now, since_hours, run_date, settings, notes, failures):
    """Hacker News, Daily Papers and Telegram stories, for the sources that are turned on. A source that
    cannot be read is noted, not fatal; one that failed outright is also left in failures (name -> error)."""
    found = []
    if settings["hacker_news"]:
        found.extend(json_source_stories(
            fetch_page,
            discovery.HACKER_NEWS,
            discovery.hacker_news_search_url(now, since_hours),
            discovery.hacker_news_stories,
            settings["hacker_news_max"],
            notes,
            failures,
        ))
    if settings["daily_papers"]:
        found.extend(json_source_stories(
            fetch_page,
            discovery.DAILY_PAPERS,
            discovery.daily_papers_url(run_date - timedelta(days=1)),
            discovery.daily_paper_stories,
            settings["daily_papers_max"],
            notes,
            failures,
        ))
    if settings["telegram"]:
        channel_errors = []
        for channel in settings["telegram_channels"]:
            try:
                found.extend(discovery.telegram_stories(telegram_channel_posts(fetch_page, channel, now, since_hours), now, since_hours))
            except (sources.SourceError, KeyError, TypeError, ValueError) as error:
                notes.append(f"{discovery.TELEGRAM} @{channel} unreadable: {error}")
                channel_errors.append(f"@{channel}: {error}")
        if len(channel_errors) > 0 and len(channel_errors) == len(settings["telegram_channels"]):
            failures[discovery.TELEGRAM] = ", ".join(channel_errors)
    return found


def telegram_channel_posts(fetch_page, channel, now, since_hours):
    """Preview pages of one channel, newest first, until the oldest post is outside the window.

    If an older page cannot be read, the posts already read are kept.
    """
    posts = []
    before = None
    for page_number in range(discovery.TELEGRAM_MAX_PAGES):
        try:
            page_html = fetch_page(discovery.telegram_preview_url(channel, before))
        except sources.SourceError:
            if before is None:
                raise
            break
        page_posts = discovery.telegram_posts(page_html)
        if len(page_posts) == 0:
            break
        posts.extend(page_posts)
        oldest_post = min(page_posts, key=lambda post: post["id"])
        if oldest_post["published"] < now - timedelta(hours=since_hours):
            break
        before = oldest_post["id"]
    return posts


def pick_round_robin(candidates, max_stories, claimed_keys, resolve_candidate=None):
    """Up to max_stories of the (record, source_urls, source_keys) candidates, taking one from each source in turn
    (SOURCE_SETTING_KEYS order), each source's own order kept.

    A candidate whose source_keys are already in claimed_keys is a duplicate: it is skipped without using a slot. Picking a candidate claims its source_keys, so only stories that
    survive the cap can make another story a duplicate. A candidate whose source_keys are None is not resolved yet:
    resolve_candidate(candidate) returns it with its source_urls and source_keys filled in, or None to skip it
    without using a slot. Returns (picked, duplicates), both in candidate order.
    """
    remaining_by_source = []
    for via in SOURCE_SETTING_KEYS:
        remaining = []
        for index, candidate in enumerate(candidates):
            if candidate[0]["via"] == via:
                remaining.append((index, candidate))
        remaining_by_source.append(remaining)
    claimed_keys = set(claimed_keys)
    picked_with_index = []
    duplicates_with_index = []
    while len(picked_with_index) < max_stories:
        any_remaining = False
        for remaining in remaining_by_source:
            if len(remaining) > 0:
                any_remaining = True
            while len(remaining) > 0 and len(picked_with_index) < max_stories:
                index, candidate = remaining.pop(0)
                if candidate[2] is None:
                    candidate = resolve_candidate(candidate)
                    if candidate is None:
                        continue
                if not claimed_keys.isdisjoint(candidate[2]):
                    duplicates_with_index.append((index, candidate))
                    continue
                picked_with_index.append((index, candidate))
                claimed_keys.update(candidate[2])
                break
        if not any_remaining:
            break
    picked_with_index.sort(key=lambda pair: pair[0])
    duplicates_with_index.sort(key=lambda pair: pair[0])
    picked = []
    for index, candidate in picked_with_index:
        picked.append(candidate)
    duplicates = []
    for index, candidate in duplicates_with_index:
        duplicates.append(candidate)
    return picked, duplicates


def collect(root, run_date, now, since_hours, fetch_page=None, read_source=None, settings=None):
    """Returns (records, notes). Raises config.ConfigError when no source is turned on and EverySourceUnreadable
    when every source that is turned on failed outright."""
    if fetch_page is None:
        fetch_page = sources.http_get
    if read_source is None:
        read_source = sources.read_source
    if settings is None:
        settings = config.load_config(root)
    enabled_sources = []
    for source_name, setting_key in SOURCE_SETTING_KEYS.items():
        if settings[setting_key]:
            enabled_sources.append(source_name)
    if len(enabled_sources) == 0:
        raise config.ConfigError("no source is turned on in [sources]")
    work_dir = root / "work" / run_date.isoformat()
    seen = store.load_seen(root / "data" / "seen.json")
    published_keys = published_source_urls(root)
    candidates = []
    notes = []
    failures = {}
    used_slugs = set()
    alphasignal_stories = []
    if settings["alphasignal"]:
        try:
            alphasignal_stories = feed.select_new_stories(feed.parse_feed(fetch_page(feed.FEED_URL)), seen, now, since_hours)
        except (sources.SourceError, ElementTree.ParseError) as error:
            notes.append(f"{ALPHASIGNAL} feed unreadable: {error}")
            failures[ALPHASIGNAL] = str(error)
    for story in alphasignal_stories:  # the article page, which lists the sources, is read only if the cap reaches the story
        record = new_record(story, unique_slug(sources.path_parts(story["url"])[-1], used_slugs), ALPHASIGNAL)
        candidates.append((record, None, None))
    skip_notes = []

    def resolve_alphasignal_candidate(candidate):
        record = candidate[0]
        try:
            citation_urls = feed.extract_citations(fetch_page(record["story_url"]))
        except sources.SourceError as error:
            skip_notes.append(f"{ALPHASIGNAL} story skipped, article page unreadable: {error}: {record['headline']}")
            return None
        except Exception as error:
            skip_notes.append(f"{ALPHASIGNAL} story skipped, article page unreadable: {unexpected_error_text(error)}: {record['headline']}")
            return None
        if len(citation_urls) == 0:
            skip_notes.append(f"{ALPHASIGNAL} story skipped, article lists no sources: {record['headline']}")
            return None
        return record, citation_urls, urls.specific_page_keys(citation_urls)

    extra_notes = []
    for story in extra_stories(fetch_page, now, since_hours, run_date, settings, extra_notes, failures):
        if story["url"] in seen or story["published"] is None:
            continue
        record = new_record(story, unique_slug(story["slug"], used_slugs), story["via"])
        candidates.append((record, story["source_urls"], urls.specific_page_keys(story["source_urls"])))
    if len(failures) == len(enabled_sources):
        error_texts = []
        for source_name in enabled_sources:
            error_texts.append(f"{source_name}: {failures[source_name]}")
        raise EverySourceUnreadable("; ".join(error_texts))
    picked, duplicates = pick_round_robin(candidates, settings["max_stories_per_day"], published_keys, resolve_alphasignal_candidate)
    notes.extend(skip_notes)
    notes.extend(extra_notes)
    for record, source_urls, source_keys in duplicates:
        notes.append(f"{record['via']} story already covered: {record['headline']}")
    records = []
    for record, source_urls, source_keys in picked:
        records.append(record)
        save_sources(root, work_dir, record, source_urls, read_source)
    store.write_json_atomic(work_dir / "stories.json", records)
    return records, notes


def has_readable_source(record):
    for source_record in record["sources"]:
        if source_record["file"] is not None:
            return True
    return False


def main():
    store.use_utf8_console()
    parser = argparse.ArgumentParser(description="Collect new stories and their primary sources.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--since-hours", type=int, default=48)
    arguments = parser.parse_args()
    log_path = arguments.root / "data" / "runs.log"
    try:
        records, notes = collect(arguments.root, arguments.date, datetime.now(timezone.utc), arguments.since_hours)
    except EverySourceUnreadable as error:
        store.append_run_log(log_path, f"collect failed: every enabled source unreadable: {error}")
        print(f"collect failed: every enabled source unreadable: {error}", file=sys.stderr)
        return 2
    except config.ConfigError as error:
        store.append_run_log(log_path, f"collect failed: config: {error}")
        print(f"collect failed: config: {error}", file=sys.stderr)
        return 3
    readable_count = 0
    problems = []
    counts_by_source = {}
    for record in records:
        counts_by_source[record["via"]] = counts_by_source.get(record["via"], 0) + 1
        if has_readable_source(record):
            readable_count += 1
        else:
            problems.append(f"{record['slug']} (no source could be read)")
    breakdown = []
    for via, count in counts_by_source.items():
        breakdown.append(f"{via} {count}")
    summary = f"collected: {len(records)} new stories ({', '.join(breakdown) or 'none'}), {readable_count} with readable sources"
    if problems:
        summary += "; unreadable: " + "; ".join(problems)
    store.append_run_log(log_path, summary)
    print(summary)
    for note in notes:
        store.append_run_log(log_path, f"collect note: {note}")
        print(f"note: {note}")
    print(f"Details: work/{arguments.date.isoformat()}/stories.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
