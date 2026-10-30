import json
from datetime import date, datetime, timedelta, timezone

import pytest

import collect
from ainews import config, feed, sources, store
from tests import helpers
from tests.helpers import make_item, write_day

FEED_XML = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Story A headline</title><link>https://alphasignal.ai/news/a</link><pubDate>Sun, 04 Oct 2026 16:00:00 GMT</pubDate></item>
<item><title>Story B headline</title><link>https://alphasignal.ai/news/b</link><pubDate>Sun, 04 Oct 2026 12:00:00 GMT</pubDate></item>
</channel></rss>"""
ARTICLE_A = (
    '<script type="application/ld+json">{"citation":["https://huggingface.co/lab/model",'
    '"https://blog.example.com/post"]}</script>'
)
NOW = datetime(2026, 10, 4, 22, 0, tzinfo=timezone.utc)
RUN_DATE = date(2026, 10, 5)
PAGES = {
    feed.FEED_URL: FEED_XML,
    "https://alphasignal.ai/news/a": ARTICLE_A,
    "https://alphasignal.ai/news/b": "<html><body>No citation block</body></html>",
}


def fetch_page(url):
    return PAGES[url]


def read_source(url):
    if "blog.example.com" in url:
        raise sources.SourceError("HTTP 403 from blog.example.com")
    return "huggingface", "README text"


def test_collect_saves_source_text_and_records_failures(tmp_path):
    records, notes = collect.collect(tmp_path, RUN_DATE, NOW, 48, fetch_page, read_source, settings=helpers.all_sources_settings())
    slugs = []
    for record in records:
        slugs.append(record["slug"])
    assert slugs == ["a"]
    assert "AlphaSignal story skipped, article lists no sources: Story B headline" in notes
    story_a = records[0]
    assert story_a["published"] == "2026-10-04"
    assert story_a["headline"] == "Story A headline"
    assert story_a["via"] == "AlphaSignal"
    assert story_a["sources"][0]["file"] == "work/2026-10-05/sources/a/1-huggingface.txt"
    saved_text = (tmp_path / story_a["sources"][0]["file"]).read_text(encoding="utf-8")
    assert saved_text == "URL: https://huggingface.co/lab/model\n\nREADME text\n"
    assert story_a["sources"][1] == {
        "url": "https://blog.example.com/post",
        "kind": "web",
        "file": None,
        "error": "HTTP 403 from blog.example.com",
    }
    saved_records = json.loads((tmp_path / "work" / "2026-10-05" / "stories.json").read_text(encoding="utf-8"))
    assert saved_records == records


def test_collect_skips_stories_already_seen(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "seen.json").write_text(json.dumps({"https://alphasignal.ai/news/b": "2026-10-04"}), encoding="utf-8")
    records, notes = collect.collect(tmp_path, RUN_DATE, NOW, 48, fetch_page, read_source, settings=helpers.all_sources_settings())
    assert len(records) == 1
    assert records[0]["slug"] == "a"


def test_collect_records_an_unreadable_article_page(tmp_path):
    def failing_article(url):
        if url == feed.FEED_URL:
            return FEED_XML
        raise sources.SourceError("HTTP 500 from alphasignal.ai")

    records, notes = collect.collect(tmp_path, RUN_DATE, NOW, 48, failing_article, read_source, settings=helpers.all_sources_settings())
    assert records == []
    assert "AlphaSignal story skipped, article page unreadable: HTTP 500 from alphasignal.ai: Story A headline" in notes


def test_collect_main_returns_2_when_every_enabled_source_is_unreachable(tmp_path, monkeypatch, capsys):
    def unreachable(url, headers=None, timeout=30):
        raise sources.SourceError("could not reach the site: timed out")

    monkeypatch.setattr(sources, "http_get", unreachable)
    monkeypatch.setattr("sys.argv", ["collect.py", "--root", str(tmp_path), "--date", "2026-10-05"])
    assert collect.main() == 2
    assert "every enabled source unreadable" in capsys.readouterr().err
    run_log = (tmp_path / "data" / "runs.log").read_text(encoding="utf-8")
    assert "collect failed: every enabled source unreadable: Hacker News: could not reach the site" in run_log


def test_collect_main_returns_3_when_the_config_is_bad(tmp_path, monkeypatch, capsys):
    (tmp_path / "config.toml").write_text("hacker_news_max = 5\n", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["collect.py", "--root", str(tmp_path), "--date", "2026-10-05"])
    assert collect.main() == 3
    assert "collect failed: config: config.toml: unknown key 'hacker_news_max' at the top level" in capsys.readouterr().err
    assert "collect failed: config:" in (tmp_path / "data" / "runs.log").read_text(encoding="utf-8")


def test_collect_main_returns_0_when_no_story_is_new(tmp_path, monkeypatch, capsys):
    def empty_sources(url, headers=None, timeout=30):
        if url.startswith("https://hn.algolia.com/"):
            return json.dumps({"hits": []})
        return json.dumps([])

    monkeypatch.setattr(sources, "http_get", empty_sources)
    monkeypatch.setattr("sys.argv", ["collect.py", "--root", str(tmp_path), "--date", "2026-10-05"])
    assert collect.main() == 0
    assert "collected: 0 new stories (none)" in capsys.readouterr().out


def test_collect_records_unexpected_source_errors_instead_of_crashing(tmp_path):
    def broken_reader(url):
        if "blog.example.com" in url:
            raise UnicodeEncodeError("ascii", "café", 3, 4, "ordinal not in range(128)")
        return "huggingface", "README text"

    records, notes = collect.collect(tmp_path, RUN_DATE, NOW, 48, fetch_page, broken_reader, settings=helpers.all_sources_settings())
    story_a = records[0]
    assert story_a["sources"][0]["file"] is not None
    assert story_a["sources"][1]["file"] is None
    assert story_a["sources"][1]["error"].startswith("unexpected error: UnicodeEncodeError")
    assert (tmp_path / "work" / "2026-10-05" / "stories.json").exists()


def test_collect_records_unexpected_article_errors_instead_of_crashing(tmp_path):
    def broken_article(url):
        if url == feed.FEED_URL:
            return FEED_XML
        raise ValueError("Invalid IPv6 URL")

    records, notes = collect.collect(tmp_path, RUN_DATE, NOW, 48, broken_article, read_source, settings=helpers.all_sources_settings())
    assert records == []
    assert "AlphaSignal story skipped, article page unreadable: unexpected error: ValueError: Invalid IPv6 URL: Story A headline" in notes


HN_PAYLOAD = {"hits": [
    {"objectID": "77", "title": "Kolibri-2 LLM released", "url": "https://github.com/lab/kolibri", "points": 240,
     "created_at_i": int(NOW.timestamp()) - 3600},
    {"objectID": "78", "title": "Lab model card discussion", "url": "https://huggingface.co/lab/model", "points": 180,
     "created_at_i": int(NOW.timestamp()) - 3600},
]}
PAPERS_PAYLOAD = [
    {"paper": {"id": "2610.08077", "title": "Self-Retrospection Distillation", "upvotes": 126,
               "publishedAt": "2026-10-03T17:00:00.000Z"}},
]


def telegram_page(channel, posts):
    """A preview page in t.me/s markup; posts are (id, published, text html)."""
    blocks = []
    for post_id, published, text_html in posts:
        blocks.append(
            f'<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message" '
            f'data-post="{channel}/{post_id}"><div class="tgme_widget_message_text js-message_text" dir="auto">'
            f'{text_html}</div><div class="tgme_widget_message_footer"><time datetime="{published.isoformat()}">'
            f'</time></div></div></div>'
        )
    return "<html><body>" + "".join(blocks) + "</body></html>"


TELEGRAM_PAGES = {
    "https://t.me/s/neuraldvig": telegram_page("neuraldvig", [
        (5, NOW, 'Вышла ИИ-модель Kolibri-2 — <a href="https://github.com/lab/kolibri?utm_source=tg">GitHub</a>'),
        (6, NOW, 'ИИ-агент для терминала: <a href="https://github.com/ru/termagent">репозиторий</a>'),
    ]),
    "https://t.me/s/whackdoor": "<html><body></body></html>",
}


def fetch_everything(url):
    if url.startswith("https://hn.algolia.com/"):
        return json.dumps(HN_PAYLOAD)
    if url == "https://huggingface.co/api/daily_papers?date=2026-10-04":
        return json.dumps(PAPERS_PAYLOAD)
    if url.startswith("https://t.me/s/"):
        return TELEGRAM_PAGES.get(url, "<html><body></body></html>")
    return PAGES[url]


def test_collect_adds_hacker_news_and_daily_papers_and_drops_duplicates(tmp_path):
    records, notes = collect.collect(tmp_path, RUN_DATE, NOW, 48, fetch_everything, read_source, settings=helpers.all_sources_settings())
    found = []
    for record in records:
        found.append((record["via"], record["slug"]))
    assert found == [
        ("AlphaSignal", "a"),
        ("Hacker News", "hn-77-kolibri-2-llm-released"),
        ("Hugging Face Daily Papers", "paper-2610-08077-self-retrospection-distillation"),
        ("Telegram", "tg-neuraldvig-6"),
    ]
    hacker_news_record = records[1]
    assert hacker_news_record["story_url"] == "https://news.ycombinator.com/item?id=77"
    assert hacker_news_record["note"] == "240 points"
    assert hacker_news_record["sources"][0]["url"] == "https://github.com/lab/kolibri"
    assert records[2]["sources"][0]["url"] == "https://arxiv.org/abs/2610.08077"
    assert records[3]["sources"][0]["url"] == "https://github.com/ru/termagent"
    assert records[3]["note"] == "@neuraldvig"
    assert notes == [
        "AlphaSignal story skipped, article lists no sources: Story B headline",
        "Hacker News story already covered: Lab model card discussion",
        "Telegram story already covered: Вышла ИИ-модель Kolibri-2 — GitHub",
    ]


def test_collect_drops_extra_stories_already_in_an_earlier_digest(tmp_path):
    write_day(tmp_path / "data" / "days", "2026-10-04", [make_item(
        sources=[{"label": "GitHub", "url": "https://github.com/lab/kolibri/"}],
    )])
    records, notes = collect.collect(tmp_path, RUN_DATE, NOW, 48, fetch_everything, read_source, settings=helpers.all_sources_settings())
    vias = []
    for record in records:
        vias.append(record["via"])
    assert "Hacker News" not in vias
    assert "Hacker News story already covered: Kolibri-2 LLM released" in notes


def test_collect_notes_unreadable_extra_sources_and_keeps_going(tmp_path):
    records, notes = collect.collect(tmp_path, RUN_DATE, NOW, 48, fetch_page, read_source, settings=helpers.all_sources_settings())
    assert len(records) == 1
    assert notes[0].startswith("AlphaSignal story skipped")
    assert notes[1].startswith("Hacker News unreadable:")
    assert notes[2].startswith("Hugging Face Daily Papers unreadable:")
    assert notes[3].startswith("Telegram @neuraldvig unreadable:")
    assert notes[4].startswith("Telegram @whackdoor unreadable:")


def test_telegram_channel_posts_follows_older_pages_until_the_window_ends():
    old = NOW - timedelta(hours=60)
    pages = {
        "https://t.me/s/neuraldvig": telegram_page("neuraldvig", [(20, NOW, "a"), (21, NOW, "b")]),
        "https://t.me/s/neuraldvig?before=20": telegram_page("neuraldvig", [(18, old, "c"), (19, NOW, "d")]),
    }
    requested = []

    def fetch_pages(url):
        requested.append(url)
        return pages[url]

    posts = collect.telegram_channel_posts(fetch_pages, "neuraldvig", NOW, 48)
    assert requested == ["https://t.me/s/neuraldvig", "https://t.me/s/neuraldvig?before=20"]
    assert len(posts) == 4


def test_telegram_channel_posts_keeps_what_it_read_when_an_older_page_fails():
    def fetch_pages(url):
        if url == "https://t.me/s/neuraldvig":
            return telegram_page("neuraldvig", [(20, NOW, "a")])
        raise sources.SourceError("HTTP 429 from t.me")

    assert len(collect.telegram_channel_posts(fetch_pages, "neuraldvig", NOW, 48)) == 1


def article_citing(*cited_urls):
    return '<script type="application/ld+json">{"citation":' + json.dumps(list(cited_urls)) + "}</script>"


def hacker_news_front_page_payload():
    return {"hits": [
        {"objectID": "79", "title": "Kolibri-3 LLM released", "url": "https://github.com", "points": 200,
         "created_at_i": int(NOW.timestamp()) - 3600},
    ]}


def test_collect_does_not_treat_a_cited_front_page_as_a_duplicate_source(tmp_path):
    pages = dict(PAGES)
    pages["https://alphasignal.ai/news/a"] = article_citing("https://github.com", "https://blog.example.com/post")

    def fetch_front_page_case(url):
        if url.startswith("https://hn.algolia.com/"):
            return json.dumps(hacker_news_front_page_payload())
        if url in pages:
            return pages[url]
        return fetch_everything(url)

    records, notes = collect.collect(tmp_path, RUN_DATE, NOW, 48, fetch_front_page_case, read_source, settings=helpers.all_sources_settings())
    slugs = []
    for record in records:
        slugs.append(record["slug"])
    assert "hn-79-kolibri-3-llm-released" in slugs
    assert "Hacker News story already covered: Kolibri-3 LLM released" not in notes


def test_collect_does_not_treat_a_front_page_in_an_earlier_digest_as_a_duplicate_source(tmp_path):
    write_day(tmp_path / "data" / "days", "2026-10-04", [make_item(
        sources=[{"label": "GitHub", "url": "https://github.com/lab/kolibri"}, {"label": "Site", "url": "https://github.com/"}],
    )])

    def fetch_front_page_case(url):
        if url.startswith("https://hn.algolia.com/"):
            return json.dumps(hacker_news_front_page_payload())
        return fetch_everything(url)

    records, notes = collect.collect(tmp_path, RUN_DATE, NOW, 48, fetch_front_page_case, read_source, settings=helpers.all_sources_settings())
    vias = []
    for record in records:
        vias.append(record["via"])
    assert "Hacker News" in vias
    assert "Hacker News story already covered: Kolibri-3 LLM released" not in notes


def test_collect_gives_every_story_a_unique_slug_within_80_characters(tmp_path):
    long_name = "x" * 80
    feed_xml = (
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        f"<item><title>One</title><link>https://alphasignal.ai/news/{long_name}-one</link><pubDate>Sun, 04 Oct 2026 12:00:00 GMT</pubDate></item>"
        f"<item><title>Two</title><link>https://alphasignal.ai/news/{long_name}-two</link><pubDate>Sun, 04 Oct 2026 13:00:00 GMT</pubDate></item>"
        f"<item><title>Three</title><link>https://alphasignal.ai/news/{long_name}-three</link><pubDate>Sun, 04 Oct 2026 14:00:00 GMT</pubDate></item>"
        "</channel></rss>"
    )

    def fetch_long_slugs(url):
        if url == feed.FEED_URL:
            return feed_xml
        return article_citing("https://huggingface.co/lab/model-" + url[-3:])

    records, notes = collect.collect(tmp_path, RUN_DATE, NOW, 48, fetch_long_slugs, read_source, settings=helpers.all_sources_settings())
    slugs = []
    for record in records:
        slugs.append(record["slug"])
    assert slugs == [long_name, long_name[:78] + "-2", long_name[:78] + "-3"]
    for record in records:
        assert (tmp_path / record["sources"][0]["file"]).exists()


def test_collect_leaves_no_temporary_files_beside_the_saved_sources(tmp_path):
    collect.collect(tmp_path, RUN_DATE, NOW, 48, fetch_page, read_source, settings=helpers.all_sources_settings())
    leftovers = list((tmp_path / "work").rglob("*.tmp"))
    assert leftovers == []


def test_has_readable_source_needs_one_saved_file():
    assert collect.has_readable_source({"sources": [{"file": None}, {"file": "work/x/1-web.txt"}]}) is True
    assert collect.has_readable_source({"sources": [{"file": None}]}) is False
    assert collect.has_readable_source({"sources": []}) is False


def test_unexpected_error_text_names_the_error_type():
    assert collect.unexpected_error_text(KeyError("k")) == "unexpected error: KeyError: 'k'"


def test_unique_slug_stays_within_the_limit_and_counts_up():
    used_slugs = set()
    assert collect.unique_slug("a" * 100, used_slugs) == "a" * 80
    assert collect.unique_slug("a" * 100, used_slugs) == "a" * 78 + "-2"
    assert collect.unique_slug("short", used_slugs) == "short"
    assert collect.unique_slug("short", used_slugs) == "short-2"


def test_unique_slug_keeps_only_letters_digits_and_hyphens():
    used_slugs = set()
    assert collect.unique_slug("..", used_slugs) == "story"
    assert collect.unique_slug("..", used_slugs) == "story-2"
    assert collect.unique_slug("Kimi_K3.1 Pro", used_slugs) == "kimi-k3-1-pro"


def test_save_sources_refuses_a_folder_outside_the_sources_folder(tmp_path):
    work_dir = tmp_path / "work" / "2026-10-05"
    keep_file = work_dir / "stories.json"
    keep_file.parent.mkdir(parents=True)
    keep_file.write_text("[]", encoding="utf-8")
    record = {"slug": "..", "sources": []}
    with pytest.raises(ValueError):
        collect.save_sources(tmp_path, work_dir, record, ["https://example.com/page"], read_source)
    assert keep_file.exists()


def test_collect_clears_a_stories_old_source_files_before_saving(tmp_path):
    stale_file = tmp_path / "work" / "2026-10-05" / "sources" / "a" / "3-web.txt"
    stale_file.parent.mkdir(parents=True)
    stale_file.write_text(sources.format_source_file("https://old.example.com/page", "old text"), encoding="utf-8")
    collect.collect(tmp_path, RUN_DATE, NOW, 48, fetch_page, read_source, settings=helpers.all_sources_settings())
    assert not stale_file.exists()
    listed_names = []
    for source_path in sources.story_source_files(stale_file.parent):
        listed_names.append(source_path.name)
    assert listed_names == ["1-huggingface.txt"]


def test_collect_with_the_defaults_reads_no_alphasignal_or_telegram(tmp_path):
    requested = []

    def recording_fetch(url):
        requested.append(url)
        return fetch_everything(url)

    collect.collect(tmp_path, RUN_DATE, NOW, 48, recording_fetch, read_source, settings=config.DEFAULTS)
    assert len(requested) == 2
    for url in requested:
        assert "alphasignal.ai" not in url
        assert "t.me/s/" not in url


def test_collect_without_alphasignal_still_collects_hacker_news_and_papers(tmp_path):
    settings = helpers.all_sources_settings()
    settings["alphasignal"] = False
    settings["telegram"] = False
    records, notes = collect.collect(tmp_path, RUN_DATE, NOW, 48, fetch_everything, read_source, settings=settings)
    vias = []
    for record in records:
        vias.append(record["via"])
    assert vias == ["Hacker News", "Hacker News", "Hugging Face Daily Papers"]


def test_collect_raises_when_every_enabled_source_is_unreadable(tmp_path):
    def unreachable(url):
        raise sources.SourceError("HTTP 503")

    with pytest.raises(collect.EverySourceUnreadable) as raised:
        collect.collect(tmp_path, RUN_DATE, NOW, 48, unreachable, read_source, settings=helpers.all_sources_settings())
    message = str(raised.value)
    assert "AlphaSignal" in message
    assert "Hacker News" in message
    assert "Hugging Face Daily Papers" in message
    assert "Telegram" in message
    assert "@neuraldvig" in message
    assert "@whackdoor" in message


def test_collect_raises_a_config_error_when_no_source_is_turned_on(tmp_path):
    settings = helpers.all_sources_settings()
    settings["alphasignal"] = False
    settings["hacker_news"] = False
    settings["daily_papers"] = False
    settings["telegram"] = False
    with pytest.raises(config.ConfigError, match=r"no source is turned on in \[sources\]"):
        collect.collect(tmp_path, RUN_DATE, NOW, 48, fetch_everything, read_source, settings=settings)


def test_collect_with_one_unreadable_source_only_notes_it(tmp_path):
    def hacker_news_down(url):
        if url.startswith("https://hn.algolia.com/"):
            raise sources.SourceError("HTTP 503")
        return fetch_everything(url)

    records, notes = collect.collect(tmp_path, RUN_DATE, NOW, 48, hacker_news_down, read_source, settings=helpers.all_sources_settings())
    assert "Hacker News unreadable: HTTP 503" in notes
    vias = []
    for record in records:
        vias.append(record["via"])
    assert "Hugging Face Daily Papers" in vias


def test_collect_with_an_unreadable_feed_notes_it_when_another_source_works(tmp_path):
    def feed_down(url):
        if url == feed.FEED_URL:
            raise sources.SourceError("HTTP 502")
        return fetch_everything(url)

    records, notes = collect.collect(tmp_path, RUN_DATE, NOW, 48, feed_down, read_source, settings=helpers.all_sources_settings())
    assert "AlphaSignal feed unreadable: HTTP 502" in notes
    assert len(records) > 0


def test_collect_with_one_telegram_channel_down_notes_it_without_failing_telegram(tmp_path):
    def whackdoor_down(url):
        if url == "https://t.me/s/whackdoor":
            raise sources.SourceError("HTTP 429")
        return fetch_everything(url)

    settings = helpers.all_sources_settings()
    settings["hacker_news"] = False
    settings["daily_papers"] = False
    settings["alphasignal"] = False
    records, notes = collect.collect(tmp_path, RUN_DATE, NOW, 48, whackdoor_down, read_source, settings=settings)
    assert "Telegram @whackdoor unreadable: HTTP 429" in notes
    assert len(records) == 2


def test_collect_returns_no_records_and_no_error_when_sources_have_nothing(tmp_path):
    def empty_sources(url):
        if url.startswith("https://hn.algolia.com/"):
            return json.dumps({"hits": []})
        return json.dumps([])

    records, notes = collect.collect(tmp_path, RUN_DATE, NOW, 48, empty_sources, read_source, settings=config.DEFAULTS)
    assert (records, notes) == ([], [])
    assert json.loads((tmp_path / "work" / "2026-10-05" / "stories.json").read_text(encoding="utf-8")) == []


def test_collect_caps_the_day_with_a_round_robin_across_sources(tmp_path):
    hits = []
    papers = []
    for number in range(10):
        hits.append({"objectID": str(100 + number), "title": f"LLM story {number}", "url": f"https://github.com/lab/hn{number}",
                     "points": 500 - number, "created_at_i": int(NOW.timestamp()) - 3600})
        papers.append({"paper": {"id": f"2610.0{number}000", "title": f"Paper {number}", "upvotes": 500 - number,
                                 "publishedAt": "2026-10-03T17:00:00.000Z"}})

    def many_stories(url):
        if url.startswith("https://hn.algolia.com/"):
            return json.dumps({"hits": hits})
        return json.dumps(papers)

    seen_path = tmp_path / "data" / "seen.json"
    store.write_json_atomic(seen_path, {"https://example.com/old": "2026-10-01"})
    seen_before = seen_path.read_text(encoding="utf-8")
    settings = dict(config.DEFAULTS)
    settings["hacker_news_max"] = 10
    settings["daily_papers_max"] = 10
    settings["max_stories_per_day"] = 6
    records, notes = collect.collect(tmp_path, RUN_DATE, NOW, 48, many_stories, read_source, settings=settings)
    found = []
    for record in records:
        found.append((record["via"], record["story_url"]))
    assert found == [
        ("Hacker News", "https://news.ycombinator.com/item?id=100"),
        ("Hacker News", "https://news.ycombinator.com/item?id=101"),
        ("Hacker News", "https://news.ycombinator.com/item?id=102"),
        ("Hugging Face Daily Papers", "https://huggingface.co/papers/2610.00000"),
        ("Hugging Face Daily Papers", "https://huggingface.co/papers/2610.01000"),
        ("Hugging Face Daily Papers", "https://huggingface.co/papers/2610.02000"),
    ]
    saved_records = json.loads((tmp_path / "work" / "2026-10-05" / "stories.json").read_text(encoding="utf-8"))
    assert saved_records == records
    assert seen_path.read_text(encoding="utf-8") == seen_before
    saved_folders = []
    for folder in (tmp_path / "work" / "2026-10-05" / "sources").iterdir():
        saved_folders.append(folder.name)
    assert len(saved_folders) == 6


def test_collect_uses_the_configured_caps_and_channels(tmp_path):
    requested = []

    def recording_fetch(url):
        requested.append(url)
        return fetch_everything(url)

    settings = helpers.all_sources_settings()
    settings["alphasignal"] = False
    settings["hacker_news_max"] = 1
    settings["telegram_channels"] = ["somechannel"]
    records, notes = collect.collect(tmp_path, RUN_DATE, NOW, 48, recording_fetch, read_source, settings=settings)
    hacker_news_count = 0
    for record in records:
        if record["via"] == "Hacker News":
            hacker_news_count += 1
    assert hacker_news_count == 1
    telegram_urls = []
    for url in requested:
        if url.startswith("https://t.me/s/"):
            telegram_urls.append(url)
    assert telegram_urls == ["https://t.me/s/somechannel"]


def test_collect_dedup_only_counts_stories_that_survive_the_daily_cap(tmp_path):
    shared_link = "https://github.com/lab/shared"
    hits = [
        {"objectID": "1", "title": "First LLM story", "url": "https://github.com/lab/first", "points": 500,
         "created_at_i": int(NOW.timestamp()) - 3600},
        {"objectID": "2", "title": "Second LLM story", "url": shared_link, "points": 400,
         "created_at_i": int(NOW.timestamp()) - 3600},
    ]
    telegram_pages = {
        "https://t.me/s/somechannel": telegram_page("somechannel", [
            (9, NOW, f'Вышла ИИ-модель Shared-1 — <a href="{shared_link}">GitHub</a>'),
        ]),
    }

    def fetch_cut_story_case(url):
        if url.startswith("https://hn.algolia.com/"):
            return json.dumps({"hits": hits})
        return telegram_pages.get(url, "<html><body></body></html>")

    settings = helpers.all_sources_settings()
    settings["alphasignal"] = False
    settings["daily_papers"] = False
    settings["telegram_channels"] = ["somechannel"]
    settings["max_stories_per_day"] = 2
    records, notes = collect.collect(tmp_path, RUN_DATE, NOW, 48, fetch_cut_story_case, read_source, settings=settings)
    slugs = []
    for record in records:
        slugs.append(record["slug"])
    assert slugs == ["hn-1-first-llm-story", "tg-somechannel-9"]
    for note in notes:
        assert "already covered" not in note


def test_collect_still_drops_a_real_duplicate_among_the_picked_stories(tmp_path):
    shared_link = "https://github.com/lab/shared"
    hits = [
        {"objectID": "2", "title": "Second LLM story", "url": shared_link, "points": 400,
         "created_at_i": int(NOW.timestamp()) - 3600},
    ]
    telegram_pages = {
        "https://t.me/s/somechannel": telegram_page("somechannel", [
            (9, NOW, f'Вышла ИИ-модель Shared-1 — <a href="{shared_link}">GitHub</a>'),
        ]),
    }

    def fetch_duplicate_case(url):
        if url.startswith("https://hn.algolia.com/"):
            return json.dumps({"hits": hits})
        return telegram_pages.get(url, "<html><body></body></html>")

    settings = helpers.all_sources_settings()
    settings["alphasignal"] = False
    settings["daily_papers"] = False
    settings["telegram_channels"] = ["somechannel"]
    records, notes = collect.collect(tmp_path, RUN_DATE, NOW, 48, fetch_duplicate_case, read_source, settings=settings)
    assert len(records) == 1
    assert records[0]["via"] == "Hacker News"
    assert "Telegram story already covered: Вышла ИИ-модель Shared-1 — GitHub" in notes


def test_collect_leaves_unreadable_and_sourceless_alphasignal_stories_out_of_the_daily_cap(tmp_path):
    settings = helpers.all_sources_settings()
    settings["max_stories_per_day"] = 1
    records, notes = collect.collect(tmp_path, RUN_DATE, NOW, 48, fetch_page, read_source, settings=settings)
    slugs = []
    for record in records:
        slugs.append(record["slug"])
    assert slugs == ["a"]


def test_pick_round_robin_drops_an_alphasignal_story_whose_page_an_earlier_pick_claimed():
    first_alphasignal = ({"via": "AlphaSignal", "headline": "First"}, [], {"example.com/other"})
    second_alphasignal = ({"via": "AlphaSignal", "headline": "Second"}, [], {"example.com/shared"})
    hacker_news = ({"via": "Hacker News", "headline": "Shared on HN"}, [], {"example.com/shared"})
    picked, duplicates = collect.pick_round_robin([first_alphasignal, second_alphasignal, hacker_news], 5, set())
    assert picked == [first_alphasignal, hacker_news]
    assert duplicates == [second_alphasignal]


def test_collect_reads_only_the_alphasignal_article_pages_the_daily_cap_reaches(tmp_path):
    feed_items = ""
    pages = {}
    for number in range(5):
        story_url = f"https://alphasignal.ai/news/story-{number}"
        feed_items += f"<item><title>Story {number}</title><link>{story_url}</link><pubDate>Sun, 04 Oct 2026 1{number}:00:00 GMT</pubDate></item>"
        pages[story_url] = article_citing(f"https://blog.example.com/post-{number}")
    pages[feed.FEED_URL] = f'<?xml version="1.0"?><rss version="2.0"><channel>{feed_items}</channel></rss>'
    article_requests = []

    def fetch_counted(url):
        if url.startswith("https://alphasignal.ai/news/"):
            article_requests.append(url)
        return pages[url]

    settings = dict(config.DEFAULTS)
    settings["hacker_news"] = False
    settings["daily_papers"] = False
    settings["alphasignal"] = True
    settings["max_stories_per_day"] = 2
    records, notes = collect.collect(tmp_path, RUN_DATE, NOW, 48, fetch_counted, read_source, settings=settings)
    assert len(records) == 2
    assert len(article_requests) == 2


def test_collect_gives_an_alphasignal_link_without_a_path_a_slug_from_its_title(tmp_path):
    feed_xml = (
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        "<item><title>Pathless Story</title><link>https://alphasignal.ai/?p=123</link><pubDate>Sun, 04 Oct 2026 12:00:00 GMT</pubDate></item>"
        "</channel></rss>"
    )

    def fetch_pathless(url):
        if url == feed.FEED_URL:
            return feed_xml
        return article_citing("https://huggingface.co/lab/model")

    records, notes = collect.collect(tmp_path, RUN_DATE, NOW, 48, fetch_pathless, read_source, settings=helpers.all_sources_settings())
    slugs = []
    for record in records:
        slugs.append(record["slug"])
    assert slugs == ["pathless-story"]
