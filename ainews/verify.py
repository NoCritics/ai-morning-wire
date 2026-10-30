"""Checks that summarized items stay true to the source files they were written from."""
import re
from datetime import date
from pathlib import Path

from ainews import sources, urls

NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def read_story_sources(story_dir):
    """(normalized URLs, combined text) of every source file saved for one story.

    Each file starts with "URL: <url>": collector reads, browser captures and fetch-tool fallbacks alike.
    A browser capture that browser_source.py has not converted (raw, or a bot-check page) is left out.
    """
    story_dir = Path(story_dir)
    source_keys = set()
    texts = []
    if story_dir.is_dir():
        for source_path in sources.story_source_files(story_dir):
            text = source_path.read_text(encoding="utf-8", errors="replace")
            source_url = sources.source_file_url(text)
            if source_url is not None:
                source_keys.add(urls.normalize_url(source_url))
            texts.append(text)
    return source_keys, "\n".join(texts)


def uncollected_source_urls(item, collected_urls):
    """Source URLs the item cites that were never read for its stories: invented or mistyped links."""
    missing = []
    for source in item.get("sources") or []:
        url = str(source.get("url", ""))
        if urls.normalize_url(url) not in collected_urls:
            missing.append(url)
    return missing


def date_in_text(found_date, text):
    """True when the text writes this date as 2026-10-12, Oct 12, 2026, Oct. 12, 2026, October 12, 2026 or 12 October 2026; a day below 10 may have a leading zero."""
    short_month = found_date.strftime("%b")
    long_month = found_date.strftime("%B")
    month_names = f"{short_month}|{long_month}"
    if found_date.month == 9:
        month_names += "|Sept"
    day = found_date.day
    year = found_date.year
    day_pattern = str(day)
    if day < 10:
        day_pattern = f"0?{day}"
    patterns = [
        found_date.isoformat(),
        rf"\b(?:{month_names})\.? {day_pattern}(?:st|nd|rd|th)?,? {year}\b",
        rf"\b{day_pattern} (?:{month_names})\.?,? {year}\b",
    ]
    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def checked_texts(item):
    """Every text whose numbers must come from the sources: the title, the bullets and the whole PR check.
    Background notes are included too: they may add context in words, never numbers."""
    texts = [str(item.get("title", ""))]
    for bullet in item.get("bullets") or []:
        texts.append(str(bullet))
    pr_check = item.get("pr_check")
    if isinstance(pr_check, dict):
        texts.append(str(pr_check.get("why", "")))
        texts.append(str(pr_check.get("holds_up", "")))
        for note in pr_check.get("notes") or []:
            if isinstance(note, dict):
                texts.append(str(note.get("text", "")))
    return texts


def unmatched_numbers(item, source_text):
    """(number, text) pairs whose number appears nowhere in the item's sources, ignoring thousands commas.

    A date written 2026-10-12 counts as one value: it matches when the sources hold the same date in any
    common form, and is reported whole when they don't.
    """
    comparable_text = source_text.replace(",", "")
    unmatched = []
    for text in checked_texts(item):
        remaining_text = text
        for date_match in ISO_DATE.finditer(text):
            try:
                found_date = date.fromisoformat(date_match.group(0))
            except ValueError:
                continue
            if not date_in_text(found_date, source_text):
                unmatched.append((date_match.group(0), text))
            remaining_text = remaining_text.replace(date_match.group(0), " ")
        for number in NUMBER.findall(remaining_text):
            if number.replace(",", "") not in comparable_text:
                unmatched.append((number, text))
    return unmatched


def comparable_words(text):
    """Lowercase words with curly quotes and apostrophes made straight and spacing collapsed."""
    straightened = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return " ".join(straightened.lower().split())


def pitch_in_sources(pitch, source_text):
    """True when the quoted pitch appears word for word in the sources; an ellipsis may join quoted parts."""
    comparable_source = comparable_words(source_text)
    for part in re.split(r"\s*(?:…|\.\.\.)\s*", pitch):
        if part.strip() != "" and comparable_words(part) not in comparable_source:
            return False
    return True


def earlier_repeats(item, earlier_items):
    """(date, title, shared URL) for earlier items that cite the same specific page: likely the same release."""
    source_urls = []
    for source in item.get("sources") or []:
        source_urls.append(str(source.get("url", "")))
    item_keys = urls.specific_page_keys(source_urls)
    repeats = []
    for earlier_date, earlier_item in earlier_items:
        for source in earlier_item.get("sources") or []:
            if urls.normalize_url(str(source.get("url", ""))) in item_keys:
                repeats.append((earlier_date, earlier_item.get("title", ""), source["url"]))
                break
    return repeats
