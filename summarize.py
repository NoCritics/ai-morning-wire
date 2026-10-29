"""Summarize collected stories with the read-only news-summarizer agent, one batch per process.

Usage: python summarize.py [--root DIR] [--date YYYY-MM-DD] [--only N ...] BATCHES_FILE
BATCHES_FILE is a JSON array of batches, each an array of story slugs from work/<date>/stories.json,
e.g. [["slug-a", "slug-b", "slug-c"], ["slug-d", "slug-e"]].

Each batch runs as a headless `claude -p --agent news-summarizer` limited to Read, Grep and Glob and no
MCP servers, because the source files are untrusted web pages. The agent replies with a JSON array;
this script saves it as work/<date>/items-<N>.json and the full reply as work/<date>/replies/reply-<N>.md.
Exit code 1 when any batch failed; rerun just those with --only.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from ainews import sources, store

AGENT_NAME = "news-summarizer"
BATCH_TIMEOUT_SECONDS = 900
MAX_PARALLEL_BATCHES = 3
ITEMS_FILE_NAME = re.compile(r"items-(\d+)\.json")
JSON_BLOCK = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)


class BatchError(Exception):
    """A batch produced no usable items file; the message says why."""


def story_source_paths(work_dir, story):
    """Absolute paths of every readable source for a story: collector files plus converted browser and fetch-tool captures."""
    paths = []
    for source_path in sources.story_source_files(work_dir / "sources" / story["slug"]):
        paths.append(str(source_path.resolve()))
    return paths


def batch_prompt(work_dir, stories):
    lines = ["Stories:"]
    for story in stories:
        via = story["via"]
        if story.get("note"):
            via += f" ({story['note']})"
        lines.append(f"- slug: {story['slug']}")
        lines.append(f"  found via: {via}")
        lines.append(f"  headline: {story['headline']}")
        lines.append(f"  story_url: {story['story_url']}")
        lines.append(f"  published: {story['published']}")
        lines.append("  source files:")
        for source_path in story_source_paths(work_dir, story):
            lines.append(f"    {source_path}")
    return "\n".join(lines) + "\n"


def items_from_reply(reply_text):
    """The JSON array in the agent's ```json block."""
    block_match = JSON_BLOCK.search(reply_text)
    if block_match is None:
        raise BatchError("the reply has no ```json block")
    try:
        items = json.loads(block_match.group(1))
    except json.JSONDecodeError as error:
        raise BatchError(f"the ```json block is not valid JSON: {error}") from None
    if not isinstance(items, list):
        raise BatchError("the ```json block is not a JSON array")
    return items


def resolve_batches(batch_slugs, stories):
    """Story records per batch, in order. Raises store.DataError for an unknown or repeated slug."""
    stories_by_slug = {}
    for story in stories:
        stories_by_slug[story["slug"]] = story
    batches = []
    used_slugs = set()
    for slugs in batch_slugs:
        batch = []
        for slug in slugs:
            if slug not in stories_by_slug:
                raise store.DataError(f"batches file: {slug} is not in stories.json")
            if slug in used_slugs:
                raise store.DataError(f"batches file: {slug} is in more than one batch")
            used_slugs.add(slug)
            batch.append(stories_by_slug[slug])
        batches.append(batch)
    return batches


def unbatched_readable_slugs(work_dir, stories, batches):
    """Stories with readable sources that no batch covers: probably forgotten."""
    batched = set()
    for batch in batches:
        for story in batch:
            batched.add(story["slug"])
    forgotten = []
    for story in stories:
        if story["slug"] not in batched and story_source_paths(work_dir, story):
            forgotten.append(story["slug"])
    return forgotten


def run_agent(root, prompt):
    """One headless agent run; returns its reply text."""
    claude_path = shutil.which("claude")
    if claude_path is None:
        raise BatchError("the claude command is not on PATH")
    command = [
        claude_path, "-p",
        "--agent", AGENT_NAME,
        "--model", "sonnet",
        "--tools", "Read,Grep,Glob",
        "--strict-mcp-config",
    ]
    try:
        completed = subprocess.run(
            command, input=prompt, cwd=root, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=BATCH_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise BatchError(f"no reply within {BATCH_TIMEOUT_SECONDS} seconds") from None
    if completed.returncode != 0:
        raise BatchError(f"claude exited with {completed.returncode}: {completed.stderr.strip()[:300]}")
    return completed.stdout


def summarize_batch(root, work_dir, number, stories, agent_runner):
    """Run one batch and save its files. Returns (number, item count, reply text).

    Any items file left by an earlier run is deleted first, so a failed rerun leaves none behind.
    """
    (work_dir / f"items-{number}.json").unlink(missing_ok=True)
    reply_text = agent_runner(root, batch_prompt(work_dir, stories))
    store.write_text_atomic(work_dir / "replies" / f"reply-{number}.md", reply_text)
    items = items_from_reply(reply_text)
    store.write_json_atomic(work_dir / f"items-{number}.json", items)
    return number, len(items), reply_text


def summarize(root, run_date, batch_slugs, only_numbers=None, agent_runner=run_agent):
    """Returns (results, failures, forgotten): results [(number, item count, reply)], failures [(number, reason)]."""
    work_dir = root / "work" / run_date.isoformat()
    stories = store.read_json(work_dir / "stories.json")
    batches = resolve_batches(batch_slugs, stories)
    forgotten = unbatched_readable_slugs(work_dir, stories, batches)
    if not only_numbers:
        for items_path in sorted(work_dir.glob("items-*.json")):
            name_match = ITEMS_FILE_NAME.fullmatch(items_path.name)
            if name_match is not None and int(name_match.group(1)) > len(batches):
                items_path.unlink()
    jobs = []
    for number, batch in enumerate(batches, start=1):
        if only_numbers and number not in only_numbers:
            continue
        jobs.append((number, batch))
    results = []
    failures = []
    if len(jobs) == 0:
        return results, failures, forgotten
    with ThreadPoolExecutor(max_workers=min(len(jobs), MAX_PARALLEL_BATCHES)) as executor:
        futures = []
        for number, batch in jobs:
            futures.append((number, executor.submit(summarize_batch, root, work_dir, number, batch, agent_runner)))
        for number, future in futures:
            try:
                results.append(future.result())
            except (BatchError, OSError) as error:
                failures.append((number, str(error)))
    return results, failures, forgotten


def main():
    store.use_utf8_console()
    parser = argparse.ArgumentParser(description="Summarize collected stories with the read-only agent.")
    parser.add_argument("batches_file", type=Path)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--only", type=int, nargs="+", help="run just these batch numbers")
    arguments = parser.parse_args()
    try:
        batch_slugs = store.read_json(arguments.batches_file)
        results, failures, forgotten = summarize(arguments.root, arguments.date, batch_slugs, arguments.only)
    except store.DataError as error:
        print(f"Summarize stopped: {error}", file=sys.stderr)
        return 1
    for slug in forgotten:
        print(f"warning: {slug} has readable sources but is in no batch")
    for number, item_count, reply_text in results:
        print(f"batch {number}: {item_count} items -> items-{number}.json")
        for line in reply_text.split("```")[-1].strip().splitlines():
            print(f"  {line}")
    for number, reason in failures:
        print(f"batch {number} FAILED: {reason}")
    if failures:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
