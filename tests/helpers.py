"""Shared builders for the tests."""
import json

from ainews import config

PAGE_TEXT = "The lab released a new model today with a long enough description to read as a real page. " * 4


def all_sources_settings():
    """The default settings with all four sources turned on."""
    settings = dict(config.DEFAULTS)
    settings["telegram_channels"] = list(config.DEFAULTS["telegram_channels"])
    settings["alphasignal"] = True
    settings["telegram"] = True
    return settings


def converted_browser_capture(url, page_text=PAGE_TEXT):
    """A *-browser.txt as browser_source.py leaves it."""
    return f"URL: {url}\n\n(text read in a browser)\n\n{page_text}\n"


def raw_browser_capture(url, page_text=PAGE_TEXT):
    """A *-browser.txt as the browser tool writes it: the page script's return value as a JSON string."""
    return json.dumps(f"URL: {url}\n\n(text read in a browser)\n\n{page_text}")


def make_item(**overrides):
    item = {
        "id": "xing4-0-29b-a4b",
        "title": "China Telecom Xing4.0-29B-A4B: MoE model for local coding agents",
        "type": "model-release",
        "bullets": [
            "29B total / 4B active per token (MoE)",
            "256K native context",
            "License: Apache-2.0",
        ],
        "sources": [
            {"label": "Hugging Face", "url": "https://huggingface.co/Venastine-Research/Xing4.0-29B-A4B-GGUF"}
        ],
        "published": "2026-10-04",
        "story_urls": ["https://alphasignal.ai/news/xing"],
    }
    item.update(overrides)
    return item


def write_day(days_dir, day_date, items):
    days_dir.mkdir(parents=True, exist_ok=True)
    day_path = days_dir / f"{day_date}.json"
    day_path.write_text(json.dumps({"date": day_date, "items": items}), encoding="utf-8")
    return day_path


def make_template(template_dir):
    template_dir.mkdir(parents=True, exist_ok=True)
    (template_dir / "page.html").write_text(
        "<title>Test</title><style>/*__CSS__*/</style>"
        '<script type="application/json" id="news-data">{{NEWS_DATA}}</script>'
        "<script>/*__JS__*/</script>",
        encoding="utf-8",
    )
    (template_dir / "page.css").write_text("body{}", encoding="utf-8")
    (template_dir / "page.js").write_text("void 0;", encoding="utf-8")
