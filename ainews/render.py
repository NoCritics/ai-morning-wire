"""Turn validated data into the page."""
from pathlib import Path

from ainews.store import ARCHIVE_DAYS, FEED_DAYS, compact_json, day_age

STANDALONE_HEAD = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
    "</head><body>"
)
STANDALONE_TAIL = "</body></html>\n"
BACKSLASH = chr(92)
LINE_SEPARATOR = chr(0x2028)
PARAGRAPH_SEPARATOR = chr(0x2029)


def split_days(days, today):
    """Days up to FEED_DAYS old go to the feed, up to ARCHIVE_DAYS to the archive."""
    feed_days = []
    archive_days = []
    for day in days:
        age = day_age(day["date"], today)
        if age <= FEED_DAYS:
            feed_days.append(day)
        elif age <= ARCHIVE_DAYS:
            archive_days.append(day)
    return feed_days, archive_days


def embed_json(data):
    """JSON that is safe inside a <script> element: no '<', no line separators."""
    text = compact_json(data).replace("<", BACKSLASH + "u003c")
    text = text.replace(LINE_SEPARATOR, BACKSLASH + "u2028")
    return text.replace(PARAGRAPH_SEPARATOR, BACKSLASH + "u2029")


def fill_template(template_dir, style_element, script_element, news_data):
    """page.html with its style element, script element and news-data placeholder filled in."""
    page = (Path(template_dir) / "page.html").read_text(encoding="utf-8")
    page = page.replace("<style>/*__CSS__*/</style>", style_element)
    page = page.replace("<script>/*__JS__*/</script>", script_element)
    return page.replace("{{NEWS_DATA}}", embed_json(news_data))


def render_page(template_dir, feed_days, archive_days, bookmarks, today, status, digest_time):
    """The whole page with every day embedded; standalone_page() wraps it into the local ai-news.html.

    feed_days and archive_days are the days as split_days returns them. status is today's
    {"result", "items", "failedReason"}; digest_time is "HH:MM", or None when config.toml is broken.
    """
    css = (Path(template_dir) / "page.css").read_text(encoding="utf-8")
    js = (Path(template_dir) / "page.js").read_text(encoding="utf-8")
    return fill_template(
        template_dir,
        f"<style>{css}</style>",
        f"<script>{js}</script>",
        {
            "built": today.isoformat(),
            "feed": feed_days,
            "archive": archive_days,
            "bookmarks": bookmarks,
            "status": status,
            "digestTime": digest_time,
        },
    )


def render_published_page(template_dir, feed_dates, archive_dates, today, status, digest_time):
    """The page published to claude.ai: it links page.css and page.js and fetches the day files, so it embeds no items.

    feed_dates and archive_dates are day dates, newest first, as split_days orders the days.
    """
    return fill_template(
        template_dir,
        '<link rel="stylesheet" href="page.css">',
        '<script src="page.js"></script>',
        {
            "built": today.isoformat(),
            "feedDates": feed_dates,
            "archiveDates": archive_dates,
            "status": status,
            "digestTime": digest_time,
        },
    )


def standalone_page(page):
    return STANDALONE_HEAD + page + STANDALONE_TAIL
