from datetime import datetime, timezone

from ainews import feed

FEED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>AlphaSignal</title>
<item><title>Newest story</title><link>https://alphasignal.ai/news/newest</link><pubDate>Sun, 04 Oct 2026 16:01:43 GMT</pubDate></item>
<item><title>Older story</title><link>https://alphasignal.ai/news/older</link><pubDate>Sat, 03 Oct 2026 09:00:00 GMT</pubDate></item>
<item><title>Undated story</title><link>https://alphasignal.ai/news/undated</link><pubDate>sometime</pubDate></item>
<item><title>Offset story</title><link>https://alphasignal.ai/news/offset</link><pubDate>Sun, 04 Oct 2026 10:00:00 -0000</pubDate></item>
<item><title>Stale story</title><link>https://alphasignal.ai/news/stale</link><pubDate>Wed, 30 Sep 2026 08:02:42 GMT</pubDate></item>
<item><title>No link</title><pubDate>Sun, 04 Oct 2026 11:00:00 GMT</pubDate></item>
</channel></rss>"""
NOW = datetime(2026, 10, 4, 22, 0, tzinfo=timezone.utc)

ARTICLE_HTML = """<html><head>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"NewsArticle","citation":["https://huggingface.co/a/b?utm_source=alphasignal","https://arxiv.org/abs/2512.24157","https://huggingface.co/a/b"]}</script>
<script type="application/ld+json">{"@type":"BreadcrumbList","itemListElement":[]}</script>
<script type="application/ld+json">{broken json</script>
</head><body></body></html>"""


def story_urls(stories):
    urls = []
    for story in stories:
        urls.append(story["url"])
    return urls


def test_parse_feed_reads_every_item():
    stories = feed.parse_feed(FEED_XML)
    assert story_urls(stories) == [
        "https://alphasignal.ai/news/newest",
        "https://alphasignal.ai/news/older",
        "https://alphasignal.ai/news/undated",
        "https://alphasignal.ai/news/offset",
        "https://alphasignal.ai/news/stale",
    ]
    assert stories[0]["title"] == "Newest story"
    assert stories[0]["published"] == datetime(2026, 10, 4, 16, 1, 43, tzinfo=timezone.utc)
    assert stories[2]["published"] is None
    assert stories[3]["published"] == datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)


def test_select_new_stories_skips_seen_undated_and_stale_ones_oldest_first():
    seen = {"https://alphasignal.ai/news/older": "2026-10-04"}
    selected = feed.select_new_stories(feed.parse_feed(FEED_XML), seen, NOW, since_hours=48)
    assert story_urls(selected) == [
        "https://alphasignal.ai/news/offset",
        "https://alphasignal.ai/news/newest",
    ]


def test_extract_citations_cleans_tracking_and_duplicates():
    assert feed.extract_citations(ARTICLE_HTML) == [
        "https://huggingface.co/a/b",
        "https://arxiv.org/abs/2512.24157",
    ]


def test_extract_citations_accepts_objects_and_single_strings():
    html_text = (
        '<script type="application/ld+json">{"@graph":['
        '{"citation":{"url":"https://github.com/o/r"}},'
        '{"citation":"https://x.com/u/status/1?utm_medium=x&s=20"},'
        '{"citation":"not a url"}]}</script>'
    )
    assert feed.extract_citations(html_text) == [
        "https://github.com/o/r",
        "https://x.com/u/status/1",
    ]


def test_extract_citations_without_json_ld_is_empty():
    assert feed.extract_citations("<html><body>No data</body></html>") == []


def test_extract_citations_drops_links_back_to_alphasignal():
    html_text = (
        '<script type="application/ld+json">{"citation":['
        '"https://github.com/o/r",'
        '"https://alphasignal.ai/news/another-story",'
        '"https://www.alphasignal.ai/topic/llms"]}</script>'
    )
    assert feed.extract_citations(html_text) == ["https://github.com/o/r"]
