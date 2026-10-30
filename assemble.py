"""Merge the summarizers' item files into the day file.

Usage: python assemble.py [--root DIR] [--date YYYY-MM-DD] [--accept-warnings]
Reads work/<date>/items-*.json (JSON arrays of items without ids) and writes data/days/<date>.json.
An item whose stories were all published on an earlier day is skipped, so a re-run never duplicates items;
an item that mixes published and new stories is skipped with a warning, so its new stories are not lost silently.

Every new item is checked against the source files saved for its story (ainews/verify.py):
- a cited source URL that was never read stops the run (exit 1);
- a bullet number missing from the sources, or a likely repeat of an earlier item, stops the run
  for review (exit 3) unless --accept-warnings is given.
"""
import argparse
import sys
from datetime import date
from pathlib import Path

from ainews import store, urls, verify


class ReviewNeeded(Exception):
    """Items passed validation but need a look before publishing; carries the warning lines."""

    def __init__(self, warnings):
        super().__init__(f"{len(warnings)} warnings")
        self.warnings = warnings


def load_story_slugs(work_dir):
    """{story_url: slug} from the collector's stories.json."""
    stories_path = work_dir / "stories.json"
    if not stories_path.exists():
        raise store.DataError(f"{stories_path.name} is missing: run collect.py first")
    slugs = {}
    for story in store.read_json(stories_path):
        slugs[story["story_url"]] = story["slug"]
    return slugs


def check_item(item, where, work_dir, story_slugs, earlier_items):
    """Raises store.DataError for links that were never read; returns warning lines for review."""
    collected_urls = set()
    source_texts = []
    for story_url in item["story_urls"]:
        if story_url not in story_slugs:
            raise store.DataError(f"{where}: {story_url} is not a story collected today")
        story_urls, story_text = verify.read_story_sources(work_dir / "sources" / story_slugs[story_url])
        collected_urls.update(story_urls)
        source_texts.append(story_text)
    uncollected = verify.uncollected_source_urls(item, collected_urls)
    if uncollected:
        raise store.DataError(f"{where}: cites a source that was not read for its story: {', '.join(uncollected)}")
    all_source_text = "\n".join(source_texts)
    warnings = []
    for number, text in verify.unmatched_numbers(item, all_source_text):
        warnings.append(f"{where}: number {number} is not in the sources: {text}")
    pitch = (item.get("pr_check") or {}).get("pitch")
    if pitch is not None and not verify.pitch_in_sources(pitch, all_source_text):
        warnings.append(f"{where}: the PR check's pitch is not quoted word for word from the sources: \"{pitch}\"")
    for earlier_date, earlier_title, shared_url in verify.earlier_repeats(item, earlier_items):
        warnings.append(f"{where}: may repeat {earlier_date} item \"{earlier_title}\" (both cite {shared_url})")
    return warnings


def items_file_paths(work_dir):
    """The items-N.json files of this run's batches, in batch order.

    When batches.json exists only N from 1 to its batch count are read, so a file left by an earlier run
    with more batches is ignored.
    """
    batches_path = work_dir / "batches.json"
    if not batches_path.exists():
        return sorted(work_dir.glob("items-*.json"))
    paths = []
    for batch_number in range(1, len(store.read_json(batches_path)) + 1):
        items_path = work_dir / f"items-{batch_number}.json"
        if items_path.exists():
            paths.append(items_path)
    return paths


def next_free_id(title, used_ids):
    """The title as a slug, with -2, -3, ... added while another item or bookmark already has the id."""
    base_id = urls.slugify(title, 60)
    if base_id == "":
        base_id = "item"
    item_id = base_id
    suffix = 2
    while item_id in used_ids:
        item_id = f"{base_id}-{suffix}"
        suffix += 1
    return item_id


def assemble(root, run_date, accept_warnings=False):
    """Returns (added_items, skipped_titles, warnings).

    Raises store.DataError on bad input, or ReviewNeeded when there are warnings and accept_warnings is
    false; either way nothing is written.
    """
    days_dir = root / "data" / "days"
    day_path = days_dir / f"{run_date.isoformat()}.json"
    work_dir = root / "work" / run_date.isoformat()
    used_ids = set()
    earlier_items = []
    existing_days = store.load_days(days_dir)
    published_story_urls = store.published_story_urls(existing_days)
    for existing_day in existing_days:
        for existing_item in existing_day["items"]:
            used_ids.add(existing_item["id"])
            earlier_items.append((existing_day["date"], existing_item))
    for bookmark in store.load_bookmarks(root / "data" / "bookmarks.json"):
        used_ids.add(bookmark["id"])
    if day_path.exists():
        day = store.load_day(day_path)
    else:
        day = {"date": run_date.isoformat(), "items": []}
    added_items = []
    skipped_titles = []
    warnings = []
    story_slugs = None
    for batch_path in items_file_paths(work_dir):
        batch = store.read_json(batch_path)
        if not isinstance(batch, list):
            raise store.DataError(f"{batch_path.name}: expected a JSON array of items")
        for index, draft_item in enumerate(batch):
            where = f"{batch_path.name} item {index + 1}"
            if not isinstance(draft_item, dict):
                raise store.DataError(f"{where}: an item must be a JSON object")
            published_urls = []
            draft_story_urls = draft_item.get("story_urls") or []
            for story_url in draft_story_urls:
                if story_url in published_story_urls:
                    published_urls.append(story_url)
            if len(published_urls) > 0 and len(published_urls) == len(draft_story_urls):
                skipped_titles.append(str(draft_item.get("title", "untitled")))
                continue
            if len(published_urls) > 0:
                warnings.append(
                    f"{where}: skipped \"{draft_item.get('title', 'untitled')}\" because {published_urls[0]} was already "
                    "published, but it also covers stories that are new"
                )
                continue
            item_id = next_free_id(str(draft_item.get("title", "")), used_ids)
            item = {"id": item_id}
            for key, value in draft_item.items():
                if key != "id":
                    item[key] = value
            store.validate_item(item, where)
            if story_slugs is None:
                story_slugs = load_story_slugs(work_dir)
            warnings.extend(check_item(item, where, work_dir, story_slugs, earlier_items))
            earlier_items.append((run_date.isoformat(), item))
            used_ids.add(item_id)
            for story_url in item["story_urls"]:
                published_story_urls.add(story_url)
            day["items"].append(item)
            added_items.append(item)
    if warnings and not accept_warnings:
        raise ReviewNeeded(warnings)
    store.write_json_atomic(day_path, day)
    return added_items, skipped_titles, warnings


def mark_passed_over(root, run_date):
    """Mark stories that were summarized but got no item as seen, so collect.py stops bringing them back.

    Only batches that produced an items file count; a story whose batch failed, or that had no readable
    source and so was never batched, stays eligible for the next run. A story that any draft item cites
    got an item (even one skipped as a repeat), so it is not passed over. Returns the marked story URLs.
    """
    work_dir = root / "work" / run_date.isoformat()
    batches_path = work_dir / "batches.json"
    if not batches_path.exists():
        return []
    story_urls_by_slug = {}
    for story in store.read_json(work_dir / "stories.json"):
        story_urls_by_slug[story["slug"]] = story["story_url"]
    published_story_urls = store.published_story_urls(store.load_days(root / "data" / "days"))
    drafted_story_urls = set()
    for items_path in items_file_paths(work_dir):
        batch = store.read_json(items_path)
        if not isinstance(batch, list):
            continue
        for draft_item in batch:
            if not isinstance(draft_item, dict) or not isinstance(draft_item.get("story_urls"), list):
                continue
            for story_url in draft_item["story_urls"]:
                drafted_story_urls.add(story_url)
    passed_over = []
    for batch_number, batch_slugs in enumerate(store.read_json(batches_path), start=1):
        if not (work_dir / f"items-{batch_number}.json").exists():
            continue
        for slug in batch_slugs:
            story_url = story_urls_by_slug.get(slug)
            if story_url is not None and story_url not in published_story_urls and story_url not in drafted_story_urls:
                passed_over.append(story_url)
    if passed_over:
        seen_path = root / "data" / "seen.json"
        seen = store.load_seen(seen_path)
        for story_url in passed_over:
            if story_url not in seen:
                seen[story_url] = run_date.isoformat()
        store.write_json_atomic(seen_path, seen)
    return passed_over


def main():
    store.use_utf8_console()
    parser = argparse.ArgumentParser(description="Merge summarized items into the day file.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--accept-warnings", action="store_true", help="publish even when items have review warnings")
    arguments = parser.parse_args()
    log_path = arguments.root / "data" / "runs.log"
    try:
        added_items, skipped_titles, warnings = assemble(arguments.root, arguments.date, arguments.accept_warnings)
    except store.DataError as error:
        store.append_run_log(log_path, f"assemble stopped, nothing written: {error}")
        print(f"Assemble stopped, nothing written: {error}", file=sys.stderr)
        return 1
    except ReviewNeeded as review:
        store.append_run_log(log_path, f"assemble paused for review, nothing written: {len(review.warnings)} warnings")
        print("Review needed, nothing written:", file=sys.stderr)
        for warning in review.warnings:
            print(f"- {warning}", file=sys.stderr)
        return 3
    for warning in warnings:
        print(f"accepted warning: {warning}")
    summary = f"assembled {arguments.date.isoformat()}: {len(added_items)} items added"
    if skipped_titles:
        summary += "; already published, skipped: " + "; ".join(skipped_titles)
    passed_over = mark_passed_over(arguments.root, arguments.date)
    if passed_over:
        summary += f"; {len(passed_over)} summarized stories got no item and won't be collected again"
    store.append_run_log(log_path, summary)
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
