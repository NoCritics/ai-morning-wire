from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from ainews import discovery


def hit(object_id, title, url, points, created=1791500000):
    return {"objectID": object_id, "title": title, "url": url, "points": points, "created_at_i": created}


@pytest.mark.parametrize(
    "title, url, expected",
    [
        ("Show HN: A local LLM runner in Rust", "https://github.com/a/b", True),
        ("OpenAI fires three safety researchers", "https://techcrunch.com/x", True),
        ("Let your AI agents paint arrows on your screen", "https://github.com/a/b", True),
        ("Self-Retrospection Distillation", "https://arxiv.org/abs/2610.08077", True),
        ("Germany turns coal mines into an artificial wetland", "https://timesofindia.com/x", False),
        ("A faster SQLite backup tool", "https://github.com/a/b", False),
        ("The maintainer of curl on burnout", "https://daniel.haxx.se/blog", False),
        ("A philosophy of phishing defense", "https://blog.example.com/x", False),
        ("Phi-5 mini beats larger models", "https://example.com/phi5", True),
        ("A faster SQLite backup tool", "https://WWW.HuggingFace.co/a", True),
        ("A faster SQLite backup tool", "https://blog.anthropic.com/a", True),
        ("A faster SQLite backup tool", "https://notanthropic.com/a", False),
    ],
)
def test_is_ai_story(title, url, expected):
    assert discovery.is_ai_story(title, url) is expected


def test_hacker_news_stories_keeps_linked_ai_stories_by_points():
    payload = {"hits": [
        hit("1", "A faster SQLite backup tool", "https://github.com/a/sqlite", 500),
        hit("2", "Qwen4 runs on a phone", "https://github.com/q/qwen4", 150),
        hit("3", "Ask HN: what LLM do you use?", None, 900),
        hit("4", "Claude Code tips from the team", "https://claude.com/blog/tips", 300),
    ]}
    stories = discovery.hacker_news_stories(payload, 8)
    titles = []
    for story in stories:
        titles.append(story["title"])
    assert titles == ["Claude Code tips from the team", "Qwen4 runs on a phone"]
    assert stories[0]["url"] == "https://news.ycombinator.com/item?id=4"
    assert stories[0]["source_urls"] == ["https://claude.com/blog/tips"]
    assert stories[0]["note"] == "300 points"
    assert stories[0]["slug"] == "hn-4-claude-code-tips-from-the-team"
    assert stories[0]["published"] == datetime.fromtimestamp(1791500000, tz=timezone.utc)


def test_hacker_news_stories_skips_hits_without_a_title_or_that_are_not_objects():
    payload = {"hits": [
        hit("1", None, "https://github.com/a/llm", 500),
        hit("2", "", "https://github.com/a/llm2", 400),
        "not an object",
        None,
        hit("3", "Qwen4 runs on a phone", "https://github.com/q/qwen4", 150),
    ]}
    stories = discovery.hacker_news_stories(payload, 8)
    assert len(stories) == 1
    assert stories[0]["title"] == "Qwen4 runs on a phone"


def test_hacker_news_stories_caps_the_count():
    payload = {"hits": []}
    for number in range(12):
        payload["hits"].append(hit(str(number), f"LLM story {number}", f"https://e.com/{number}", 100 + number))
    assert len(discovery.hacker_news_stories(payload, 8)) == 8


def test_hacker_news_search_url_covers_the_window():
    now = datetime(2026, 10, 9, 4, 17, tzinfo=timezone.utc)
    url = discovery.hacker_news_search_url(now, 48)
    assert f"created_at_i>{int(now.timestamp()) - 48 * 3600}" in url
    assert "points>=100" in url


def test_daily_paper_stories_takes_the_most_upvoted_above_the_floor():
    payload = [
        {"paper": {"id": "2610.00001", "title": "Low\n  votes", "upvotes": 3, "publishedAt": "2026-10-06T10:00:00.000Z"}},
        {"paper": {"id": "2610.08077", "title": "Self-Retrospection\n Distillation", "upvotes": 126, "publishedAt": "2026-10-05T17:00:00.000Z"}},
        {"paper": {"id": "2610.09823", "title": "UltraText Bench", "upvotes": 110, "publishedAt": "2026-10-06T08:00:00.000Z"}},
    ]
    stories = discovery.daily_paper_stories(payload, 5)
    assert len(stories) == 2
    assert stories[0]["title"] == "Self-Retrospection Distillation"
    assert stories[0]["url"] == "https://huggingface.co/papers/2610.08077"
    assert stories[0]["source_urls"] == ["https://arxiv.org/abs/2610.08077"]
    assert stories[0]["published"].date() == date(2026, 10, 5)
    assert stories[0]["slug"] == "paper-2610-08077-self-retrospection-distillation"


def test_daily_paper_stories_skips_entries_that_are_not_objects():
    payload = [
        "not an object",
        None,
        {"paper": "not an object"},
        {"paper": {"id": "2610.08077", "title": "Self-Retrospection Distillation", "upvotes": 126, "publishedAt": "2026-10-05T17:00:00.000Z"}},
    ]
    stories = discovery.daily_paper_stories(payload, 5)
    assert len(stories) == 1
    assert stories[0]["url"] == "https://huggingface.co/papers/2610.08077"


def test_daily_paper_stories_of_an_unexpected_payload_is_empty():
    assert discovery.daily_paper_stories({"error": "rate limited"}, 5) == []


TELEGRAM_PAGE = (Path(__file__).parent / "fixtures" / "telegram_preview.html").read_text(encoding="utf-8")
TELEGRAM_NOW = datetime(2026, 10, 14, 0, 0, tzinfo=timezone.utc)


def test_telegram_posts_reads_preview_markup():
    posts = discovery.telegram_posts(TELEGRAM_PAGE)
    ids = []
    for post in posts:
        ids.append(post["id"])
    assert ids == [1201, 1202, 1204, 1207, 1209]
    sketchbook = posts[2]
    assert sketchbook["channel"] == "pixelwire_demo"
    assert sketchbook["published"] == datetime(2026, 10, 12, 12, 43, 50, tzinfo=timezone.utc)
    assert sketchbook["links"] == ["https://example.com/sketchbook/code"]
    assert sketchbook["text"].startswith("Sketchbook, an open-source tool")


def test_telegram_posts_marks_erid_ads_and_strips_double_escaped_tracking():
    sponsored = discovery.telegram_posts(TELEGRAM_PAGE)[0]
    assert sponsored["is_ad"] is True
    assert sponsored["links"][1] == "https://example.org/events/launch-day"


def test_telegram_skip_reasons_on_preview_posts():
    reasons = []
    for post in discovery.telegram_posts(TELEGRAM_PAGE):
        reasons.append(discovery.telegram_skip_reason(post, TELEGRAM_NOW, 48))
    assert reasons == ["ad", "no outside link", None, "not about AI", None]


def test_telegram_stories_take_the_newest_ai_posts_with_links():
    stories = discovery.telegram_stories(discovery.telegram_posts(TELEGRAM_PAGE), TELEGRAM_NOW, 48)
    assert len(stories) == 2
    aurora = stories[0]
    assert aurora["url"] == "https://t.me/pixelwire_demo/1209"
    assert aurora["slug"] == "tg-pixelwire_demo-1209"
    assert aurora["source_urls"] == ["https://example.com/northwind/aurora-3"]
    assert aurora["via"] == "Telegram"
    assert aurora["note"] == "@pixelwire_demo"
    assert aurora["title"] == "Northwind Labs ships Aurora-3, a new language model"
    assert stories[1]["slug"] == "tg-pixelwire_demo-1204"


def test_telegram_posts_split_br_separated_lines():
    aurora = discovery.telegram_posts(TELEGRAM_PAGE)[4]
    assert aurora["text"].split("\n") == [
        "Northwind Labs ships Aurora-3, a new language model",
        "Longer context and cheaper inference.",
        "Announcement: example.com/northwind/aurora-3",
    ]


def test_telegram_stories_skip_posts_outside_the_window_and_respect_the_cap():
    later = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    stories = discovery.telegram_stories(discovery.telegram_posts(TELEGRAM_PAGE), later, 48)
    assert len(stories) == 1
    assert stories[0]["slug"] == "tg-pixelwire_demo-1209"
    capped = discovery.telegram_stories(discovery.telegram_posts(TELEGRAM_PAGE), TELEGRAM_NOW, 48, max_stories=1)
    assert len(capped) == 1


def test_russian_ai_terms_count_as_ai():
    assert discovery.is_ai_story("ИИ научили вести соцсети", "https://github.com/a/b") is True
    assert discovery.is_ai_story("Новая нейросеть рисует комиксы", "https://e.com/x") is True
    assert discovery.is_ai_story("GTA 6 выходит на ПК в 2027 году", "https://gamesbeat.com/x") is False


def test_post_headline_keeps_the_first_line_and_shortens_long_ones():
    assert discovery.post_headline("\nПервая строка\nвторая") == "Первая строка"
    long_line = "слово " * 40
    headline = discovery.post_headline(long_line)
    assert len(headline) <= 141
    assert headline.endswith("…")
